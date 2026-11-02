"""Callbacks: code that ADK runs automatically around every tool call.

A callback is like a database trigger. The agent does not decide to call it
and cannot skip it. That makes callbacks the right place for rules that must
always hold, no matter what the model writes.

  before_tool_callback  runs BEFORE a tool. Return a dict to block the tool
                        (that dict becomes the tool's result); return None to
                        let the tool run normally.
  after_tool_callback   runs AFTER a tool. Return None to keep the result,
                        or a dict to replace it.

This file has two callbacks:

  protect_patient_identity  (before)  blocks SQL that reads protected columns
  audit_sql                 (after)   writes every SQL tool call to a log file

Note: ADK still runs after_tool_callback when a before_tool_callback has
blocked the tool, passing it the blocking dict as the "result".
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from google.adk.tools import BaseTool, ToolContext

from .tools import PROTECTED_COLUMNS, _connect_read_only, _hint_for_error, _validate_select

AUDIT_LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "query_audit.jsonl"

SQL_TOOLS = {"run_sql_query", "explain_query"}  # tools whose args contain SQL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_protected_columns(query: str) -> list[str]:
    """Returns the protected columns a query reads, as 'table.column' strings.

    Instead of searching the SQL text (which aliases, SELECT * and CTEs can
    fool), this asks SQLite itself. SQLite's authorizer is called for every
    column a statement reads while the statement is being compiled.
    EXPLAIN compiles the query without running it, so no data is read.

    Raises sqlite3.Error if SQLite cannot compile the query (for example a
    misspelled column). The caller must then treat the query as unverified.
    """
    cleaned, error = _validate_select(query)
    if error:
        return []  # not a single SELECT: run_sql_query refuses it before touching the database

    found = set()

    def authorizer(action, table, column, _db, _trigger):
        if action == sqlite3.SQLITE_READ and (table, column) in PROTECTED_COLUMNS:
            found.add(f"{table}.{column}")
        return sqlite3.SQLITE_OK

    conn = _connect_read_only()
    try:
        conn.set_authorizer(authorizer)
        conn.execute(f"EXPLAIN {cleaned}").fetchall()
    finally:
        conn.close()
    return sorted(found)


def _write_audit(tool_context: ToolContext, tool_name: str, query: str,
                 outcome: str, row_count: Optional[int] = None,
                 message: Optional[str] = None) -> None:
    """Appends one JSON line to the audit log (JSON Lines: one record per line)."""
    session = getattr(tool_context, "session", None)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session_id": getattr(session, "id", None),
        "user_id": getattr(tool_context, "user_id", None),
        "agent": getattr(tool_context, "agent_name", None),
        "tool": tool_name,
        "outcome": outcome,
        "row_count": row_count,
        "query": query,
        "message": message,
    }
    try:
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass  # logging must never break the agent


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

def protect_patient_identity(tool: BaseTool, args: dict[str, Any],
                             tool_context: ToolContext) -> Optional[dict]:
    """Blocks run_sql_query when the SQL reads a protected patient column.

    Counting and grouping patients is fine; seeing who they are is not.
    """
    if tool.name != "run_sql_query":
        return None

    query = args.get("query", "")

    # Fail closed: if the privacy check itself cannot run, the query is blocked.
    # A guard that lets queries through whenever it errors is not a guard.
    try:
        protected = find_protected_columns(query)
    except Exception as e:
        message = f"Query not run: it could not be checked against the privacy policy ({e})."
        _write_audit(tool_context, tool.name, query, outcome="error", message=message)
        return {
            "status": "error",
            "blocked": True,
            "query": query,
            "message": message,
            "hint": _hint_for_error(str(e)),
        }

    if not protected:
        return None  # allowed: the tool runs normally

    message = (f"Blocked by privacy policy: the query reads protected column(s) "
               f"{', '.join(protected)}.")
    _write_audit(tool_context, tool.name, query, outcome="blocked", message=message)
    return {
        "status": "error",
        "blocked": True,
        "message": message,
        "hint": ("Patient names are protected. Identify patients by patient_id, "
                 "or answer with counts and aggregates instead. Do not retry with "
                 "the same columns; explain the restriction to the user."),
    }


def audit_sql(tool: BaseTool, args: dict[str, Any], tool_context: ToolContext,
              tool_response: Any) -> Optional[dict]:
    """Logs every SQL tool call. Never changes the result.

    ADK runs after_tool_callback even when a before_tool_callback blocked the
    tool. Blocked calls are already logged by protect_patient_identity, so they
    are skipped here to avoid writing the same call twice.
    """
    response = tool_response if isinstance(tool_response, dict) else {}
    if tool.name in SQL_TOOLS and not response.get("blocked"):
        _write_audit(
            tool_context,
            tool.name,
            args.get("query", ""),
            outcome=response.get("status", "unknown"),
            row_count=response.get("row_count"),
            message=response.get("message"),
        )
    return None  # keep the tool's original result