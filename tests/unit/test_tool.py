"""Unit tests for the agent's tools. These call the tools directly (no LLM),
so they are fast, free and deterministic.

Run with:  uv run pytest tests/unit
"""

import pytest

import setup_db
from app import tools


@pytest.fixture(scope="session", autouse=True)
def database():
    """Builds the synthetic database once if it does not exist yet."""
    if not tools.DB_PATH.exists():
        setup_db.build()


# --- get_database_schema ---------------------------------------------------

def test_schema_lists_all_tables():
    result = tools.get_database_schema()
    assert result["status"] == "success"
    assert set(result["schema"]) == {"departments", "doctors", "patients", "visits"}


def test_schema_includes_joins_and_allowed_values():
    visits = tools.get_database_schema()["schema"]["visits"]
    assert "visits.patient_id -> patients.patient_id" in visits["joins"]
    visit_type = next(c for c in visits["columns"] if c["column"] == "visit_type")
    assert "Inpatient" in visit_type["allowed_values"]


# --- run_sql_query: happy path ---------------------------------------------

def test_simple_count():
    result = tools.run_sql_query("SELECT COUNT(*) FROM visits")
    assert result["status"] == "success"
    assert result["rows"] == [[1500]]


def test_trailing_semicolon_is_allowed():
    assert tools.run_sql_query("SELECT 1;")["status"] == "success"


def test_cte_is_allowed():
    result = tools.run_sql_query("WITH t AS (SELECT 1 AS x) SELECT x FROM t")
    assert result["status"] == "success"


def test_results_are_capped():
    result = tools.run_sql_query("SELECT * FROM visits")
    assert result["row_count"] == tools.MAX_ROWS
    assert "note" in result


# --- run_sql_query: guardrails ---------------------------------------------

@pytest.mark.parametrize("query", [
    "DELETE FROM visits",
    "DROP TABLE patients",
    "UPDATE patients SET city = 'X'",
    "INSERT INTO departments VALUES (99, 'X', 1)",
    "PRAGMA writable_schema = 1",
])
def test_non_select_is_blocked(query):
    result = tools.run_sql_query(query)
    assert result["status"] == "error"


def test_multiple_statements_are_blocked():
    result = tools.run_sql_query("SELECT 1; DROP TABLE visits")
    assert result["status"] == "error"
    assert "one SQL statement" in result["message"]


def test_data_is_unchanged_after_blocked_queries():
    tools.run_sql_query("DELETE FROM visits")
    assert tools.run_sql_query("SELECT COUNT(*) FROM visits")["rows"] == [[1500]]


def test_bad_column_returns_helpful_hint():
    result = tools.run_sql_query("SELECT no_such_column FROM visits")
    assert result["status"] == "error"
    assert "get_database_schema" in result["hint"]


# --- explain_query ----------------------------------------------------------

def test_explain_returns_plan():
    result = tools.explain_query(
        "SELECT d.name, COUNT(*) FROM visits v "
        "JOIN departments d ON v.department_id = d.department_id GROUP BY d.name"
    )
    assert result["status"] == "success"
    assert len(result["plan"]) > 0


def test_explain_blocks_non_select():
    assert tools.explain_query("DROP TABLE visits")["status"] == "error"