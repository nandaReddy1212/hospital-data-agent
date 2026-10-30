"""Tools the hospital data agent can call.
 
Each function is a plain Python function. ADK reads the function name, the
docstring and the parameter types, and shows them to Gemini so it can decide
when to call the tool. That makes the docstrings part of the prompt: keep them
clear and specific.
 
Every tool returns a dict with a "status" key ("success" or "error"), so the
agent can always tell whether a call worked and recover when it did not.
 
Session state (Phase 3)
-----------------------
Tools that take a `tool_context` parameter can read and write session state.
ADK fills that parameter in automatically and hides it from the model, so the
model never sees or sends it. Keys used here:
 
  last_query          the last SELECT that ran successfully (this session)
  query_history       the last few successful queries (this session)
  user:answer_format  "table" or "text"; the user: prefix keeps it across
                      all of this user's sessions
"""

import re
import sqlite3
import time
from pathlib import Path
from typing import Optional
 
from google.adk.tools import ToolContext

DB_PATH = Path(__file__).resolve().parent.parent / "hospital.db"

MAX_ROWS = 50            # rows returned to the model per query
QUERY_TIMEOUT_SEC = 5    # stop runaway queries (e.g. accidental cross joins)
MAX_DISTINCT_VALUES = 10 # show allowed values for low-cardinality text columns
MAX_HISTORY = 5          # queries kept in query_history
ANSWER_FORMATS = ("table", "text")


# ---------------------------------------------------------------------------
# Helpers (not tools: they are not passed to the agent)
# ---------------------------------------------------------------------------

def _connect_read_only() -> sqlite3.Connection:
    """Opens the database read-only. Any write attempt fails at the database level."""
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Database not found at {DB_PATH}. Run 'python setup_db.py' first."
        )
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def _validate_select(query: str) -> tuple[str | None, str | None]:
    """Checks a query is a single read-only SELECT.

    Returns (cleaned_query, None) if valid, or (None, error_message) if not.
    """
    cleaned = query.strip().rstrip(";").strip()
    if not cleaned:
        return None, "The query is empty."

    # Only one statement: a semicolon left after stripping the trailing one
    # means the model tried to send several statements at once.
    if ";" in cleaned:
        return None, "Only one SQL statement is allowed per call. Remove the extra statements."

    first_word = cleaned.split(None, 1)[0].lower()
    if first_word not in ("select", "with"):
        return None, (
            "Only read-only SELECT queries (optionally starting with WITH) are allowed. "
            "INSERT, UPDATE, DELETE, DROP and other changes are not permitted."
        )
    return cleaned, None


def _hint_for_error(message: str) -> str:
    """Turns a raw SQLite error into advice the agent can act on."""
    msg = message.lower()
    if "no such column" in msg or "no such table" in msg:
        return "Call get_database_schema to check the exact table and column names, then retry."
    if "ambiguous column" in msg:
        return "Prefix the column with its table alias (for example v.department_id)."
    if "interrupted" in msg:
        return (f"The query ran longer than {QUERY_TIMEOUT_SEC} seconds. "
                "Check your JOIN conditions and add filters.")
    if "syntax error" in msg:
        return "Fix the SQL syntax. This database is SQLite, so use SQLite functions (e.g. strftime for dates)."
    return "Fix the query and try again."


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def get_database_schema() -> dict:
    """Returns every table in the hospital database with its columns, primary
    keys, foreign keys (how tables join), and the allowed values of
    low-cardinality text columns such as visit_type.

    Call this before writing any SQL so you use exact table names, column
    names, join keys and filter values.
    """
    try:
        conn = _connect_read_only()
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]

        schema = {}
        for table in tables:
            columns = []
            for _, name, col_type, _, _, is_pk in conn.execute(f'PRAGMA table_info("{table}")'):
                column = {"column": name, "type": col_type}
                if is_pk:
                    column["primary_key"] = True
                if col_type == "TEXT":
                    distinct = conn.execute(
                        f'SELECT DISTINCT "{name}" FROM "{table}" LIMIT {MAX_DISTINCT_VALUES + 1}'
                    ).fetchall()
                    if len(distinct) <= MAX_DISTINCT_VALUES:
                        column["allowed_values"] = sorted(v[0] for v in distinct)
                columns.append(column)

            foreign_keys = [
                f"{table}.{fk[3]} -> {fk[2]}.{fk[4]}"
                for fk in conn.execute(f'PRAGMA foreign_key_list("{table}")')
            ]
            row_count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            schema[table] = {"row_count": row_count, "columns": columns, "joins": foreign_keys}

        conn.close()
        return {"status": "success", "dialect": "SQLite", "schema": schema}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def _remember_query(tool_context: Optional[ToolContext], query: str) -> None:
    """Saves a successful query to session state so follow-up questions can build on it."""
    if tool_context is None:  # called directly, e.g. from unit tests
        return
    tool_context.state["last_query"] = query
    history = list(tool_context.state.get("query_history", []))
    history.append(query)
    # Assign a new list (not .append on the stored one) so ADK records the change.
    tool_context.state["query_history"] = history[-MAX_HISTORY:]


def run_sql_query(query: str, tool_context: Optional[ToolContext] = None) -> dict:
    """Runs ONE read-only SQLite SELECT query on the hospital database and
    returns the column names and rows (at most 50 rows).
 
    Use this to answer questions about patients, visits, doctors and
    departments. Call get_database_schema first so the column names are right.
    Prefer aggregations (COUNT, SUM, AVG, GROUP BY) over returning many raw rows.
 
    Args:
        query: A single SQLite SELECT statement (may start with WITH).
    """
    cleaned, error = _validate_select(query)
    if error:
        return {"status": "error", "message": error}
 
    try:
        conn = _connect_read_only()
        deadline = time.monotonic() + QUERY_TIMEOUT_SEC
        # SQLite calls this every 10,000 steps; returning True aborts the query.
        conn.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
 
        cur = conn.execute(cleaned)
        columns = [d[0] for d in cur.description]
        rows = cur.fetchmany(MAX_ROWS + 1)
        conn.close()
 
        _remember_query(tool_context, cleaned)
 
        truncated = len(rows) > MAX_ROWS
        result = {
            "status": "success",
            "query": cleaned,
            "columns": columns,
            "rows": [list(r) for r in rows[:MAX_ROWS]],
            "row_count": min(len(rows), MAX_ROWS),
        }
        if truncated:
            result["note"] = (f"Only the first {MAX_ROWS} rows are shown. "
                              "Aggregate or add a LIMIT if you need a summary.")
        return result
    except Exception as e:
        return {"status": "error", "query": cleaned, "message": str(e), "hint": _hint_for_error(str(e))}
    

def explain_query(query: str) -> dict:
    """Shows how SQLite will execute a SELECT query (its query plan) without
    running it. Use this when the user asks how a query works, why it might be
    slow, or whether it uses an index.

    Args:
        query: A single SQLite SELECT statement to explain.
    """
    cleaned, error = _validate_select(query)
    if error:
        return {"status": "error", "message": error}

    try:
        conn = _connect_read_only()
        plan = [row[3] for row in conn.execute(f"EXPLAIN QUERY PLAN {cleaned}")]
        conn.close()
        return {"status": "success", "query": cleaned, "plan": plan}
    except Exception as e:
        return {"status": "error", "query": cleaned, "message": str(e), "hint": _hint_for_error(str(e))}

def get_query_history(tool_context: Optional[ToolContext] = None) -> dict:
    """Returns the most recent successful SQL queries from this conversation,
    oldest first. Use this when the user refers to an earlier result that is
    not the most recent one (for example "go back to the query before that").
    """
    history = [] if tool_context is None else tool_context.state.get("query_history", [])
    return {"status": "success", "queries": list(history)}

def set_answer_format(answer_format: str, tool_context: Optional[ToolContext] = None) -> dict:
    """Saves how the user wants results shown, for this and all future
    conversations. Call this when the user says they prefer tables or plain text.
 
    Args:
        answer_format: Either "table" or "text".
    """
    value = answer_format.strip().lower()
    if value not in ANSWER_FORMATS:
        return {"status": "error", "message": f"answer_format must be one of {list(ANSWER_FORMATS)}."}
    if tool_context is not None:
        tool_context.state["user:answer_format"] = value
    return {"status": "success", "answer_format": value}