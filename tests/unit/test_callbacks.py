"""Tests for the privacy and audit callbacks. No LLM calls are made.

Run with:  uv run pytest tests/unit
"""

import json
import sqlite3

import pytest

import setup_db
from app import callbacks, tools


@pytest.fixture(scope="module", autouse=True)
def database():
    if not tools.DB_PATH.exists():
        setup_db.build()


@pytest.fixture
def audit_log(tmp_path, monkeypatch):
    """Sends audit records to a temporary file so tests never touch logs/."""
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(callbacks, "AUDIT_LOG_PATH", path)
    return path


class FakeTool:
    def __init__(self, name):
        self.name = name


class FakeSession:
    id = "session-123"


class FakeToolContext:
    agent_name = "data_analyst"
    user_id = "user-1"
    session = FakeSession()

    def __init__(self):
        self.state = {}


def _records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


# --- find_protected_columns: which queries touch patient names -------------

@pytest.mark.parametrize("query", [
    "SELECT first_name FROM patients",
    "SELECT p.last_name FROM patients p",                        # table alias
    "SELECT * FROM patients",                                    # star expands to names
    "WITH x AS (SELECT last_name AS n FROM patients) SELECT n FROM x",  # CTE
    "SELECT COUNT(*) FROM patients WHERE last_name = 'Kim'",     # filter only
    "SELECT v.visit_id FROM visits v JOIN patients p "
    "ON v.patient_id = p.patient_id ORDER BY p.first_name",       # ORDER BY only
])
def test_queries_reading_patient_names_are_detected(query):
    assert callbacks.find_protected_columns(query) != []


@pytest.mark.parametrize("query", [
    "SELECT COUNT(*) FROM patients",
    "SELECT patient_id, city FROM patients",
    "SELECT insurance_type, COUNT(*) FROM patients GROUP BY insurance_type",
    # Doctors' names use the same column names, but they are not protected.
    "SELECT d.first_name, d.last_name, COUNT(*) FROM visits v "
    "JOIN doctors d ON v.doctor_id = d.doctor_id GROUP BY d.doctor_id",
])
def test_aggregate_and_non_patient_queries_are_allowed(query):
    assert callbacks.find_protected_columns(query) == []


def test_non_select_is_left_for_the_tool_to_refuse():
    # run_sql_query rejects these before touching the database.
    assert callbacks.find_protected_columns("DROP TABLE patients") == []


def test_sql_that_cannot_compile_raises():
    with pytest.raises(sqlite3.Error):
        callbacks.find_protected_columns("SELECT no_such_column FROM patients")


# --- protect_patient_identity (before_tool_callback) ------------------------

def test_blocked_query_returns_error_and_skips_tool(audit_log):
    result = callbacks.protect_patient_identity(
        FakeTool("run_sql_query"), {"query": "SELECT first_name FROM patients"},
        FakeToolContext())
    assert result["status"] == "error"
    assert result["blocked"] is True
    assert "patients.first_name" in result["message"]
    assert "patient_id" in result["hint"]


def test_allowed_query_returns_none_so_tool_runs(audit_log):
    result = callbacks.protect_patient_identity(
        FakeTool("run_sql_query"), {"query": "SELECT COUNT(*) FROM patients"},
        FakeToolContext())
    assert result is None
    assert not audit_log.exists()  # allowed queries are logged after they run


def test_unverifiable_query_is_blocked_with_hint(audit_log):
    # Fail closed: a query the check cannot compile is not run.
    result = callbacks.protect_patient_identity(
        FakeTool("run_sql_query"), {"query": "SELECT no_such_column FROM patients"},
        FakeToolContext())
    assert result["status"] == "error"
    assert result["blocked"] is True
    assert "no such column" in result["message"]
    assert "get_database_schema" in result["hint"]
    [record] = _records(audit_log)
    assert record["outcome"] == "error"


def test_check_failure_blocks_instead_of_allowing(audit_log, monkeypatch):
    # If the privacy check itself breaks (e.g. the database cannot be opened),
    # even a harmless query must be blocked rather than waved through.
    def broken_connection():
        raise sqlite3.OperationalError("unable to open database file")
    monkeypatch.setattr(callbacks, "_connect_read_only", broken_connection)
    result = callbacks.protect_patient_identity(
        FakeTool("run_sql_query"), {"query": "SELECT COUNT(*) FROM patients"},
        FakeToolContext())
    assert result["blocked"] is True
    assert "unable to open database file" in result["message"]


def test_other_tools_are_not_checked():
    result = callbacks.protect_patient_identity(
        FakeTool("explain_query"), {"query": "SELECT first_name FROM patients"},
        FakeToolContext())
    assert result is None


def test_blocked_query_is_written_to_audit_log(audit_log):
    callbacks.protect_patient_identity(
        FakeTool("run_sql_query"), {"query": "SELECT last_name FROM patients"},
        FakeToolContext())
    [record] = _records(audit_log)
    assert record["outcome"] == "blocked"
    assert record["agent"] == "data_analyst"
    assert record["session_id"] == "session-123"


# --- audit_sql (after_tool_callback) -----------------------------------------

def test_successful_query_is_logged_with_row_count(audit_log):
    args = {"query": "SELECT COUNT(*) FROM visits"}
    response = tools.run_sql_query(**args)
    result = callbacks.audit_sql(FakeTool("run_sql_query"), args, FakeToolContext(), response)
    assert result is None  # the tool result is not changed
    [record] = _records(audit_log)
    assert record["outcome"] == "success"
    assert record["row_count"] == 1
    assert record["query"] == "SELECT COUNT(*) FROM visits"


def test_failed_query_is_logged_with_message(audit_log):
    args = {"query": "SELECT nope FROM visits"}
    response = tools.run_sql_query(**args)
    callbacks.audit_sql(FakeTool("run_sql_query"), args, FakeToolContext(), response)
    [record] = _records(audit_log)
    assert record["outcome"] == "error"
    assert "no such column" in record["message"]


def test_blocked_query_is_logged_only_once(audit_log):
    # ADK passes the blocking dict to after_tool_callback too; it must not
    # produce a second record.
    tool, args, ctx = FakeTool("run_sql_query"), {"query": "SELECT first_name FROM patients"}, FakeToolContext()
    blocked = callbacks.protect_patient_identity(tool, args, ctx)
    callbacks.audit_sql(tool, args, ctx, blocked)
    assert [r["outcome"] for r in _records(audit_log)] == ["blocked"]


def test_non_sql_tools_are_not_logged(audit_log):
    callbacks.audit_sql(FakeTool("get_database_schema"), {}, FakeToolContext(),
                        {"status": "success"})
    assert not audit_log.exists()


# --- schema tool --------------------------------------------------------------

def test_schema_marks_names_protected_without_sampling_values():
    columns = tools.get_database_schema()["schema"]["patients"]["columns"]
    first_name = next(c for c in columns if c["column"] == "first_name")
    assert first_name["protected"] is True
    assert "allowed_values" not in first_name