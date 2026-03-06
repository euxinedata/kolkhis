"""Structural verification tests for SessionManager.execute() response dicts.

Verifies exact keys, types, and values of the response dict for SELECT,
command, no-fetch, and error paths. No external services — uses _new_conn()
and _register_session() to create sessions without S3/Iceberg.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sessions import SessionManager


@pytest.fixture
def sm():
    """Create a SessionManager with a plain in-memory DuckDB session."""
    mgr = SessionManager()
    session_id = "test-session"
    conn, temp_dir = mgr._new_conn(session_id)
    mgr._register_session(session_id, conn, temp_dir)
    yield mgr, session_id
    mgr.close(session_id)


class TestSessionExecuteResponseStructure:
    """SELECT results."""

    def test_select_response_keys(self, sm):
        mgr, sid = sm
        result = mgr.execute(sid, "SELECT 1 AS x")
        assert set(result.keys()) == {"status", "columns", "rows", "row_count"}

    def test_select_status_value(self, sm):
        mgr, sid = sm
        result = mgr.execute(sid, "SELECT 1 AS x")
        assert result["status"] == "completed"

    def test_select_columns_shape(self, sm):
        mgr, sid = sm
        result = mgr.execute(sid, "SELECT 1 AS x, 'hello' AS y")
        columns = result["columns"]
        assert isinstance(columns, list)
        assert len(columns) == 2
        for col in columns:
            assert set(col.keys()) == {"name", "type"}

    def test_select_column_names(self, sm):
        mgr, sid = sm
        result = mgr.execute(sid, "SELECT 1 AS x, 'hello' AS y")
        names = [c["name"] for c in result["columns"]]
        assert names == ["x", "y"]

    def test_select_column_type_is_string(self, sm):
        mgr, sid = sm
        result = mgr.execute(sid, "SELECT 1 AS x")
        assert isinstance(result["columns"][0]["type"], str)

    def test_select_rows_shape(self, sm):
        mgr, sid = sm
        result = mgr.execute(sid, "SELECT 42 AS x, 'hello' AS y")
        rows = result["rows"]
        assert isinstance(rows, list)
        assert len(rows) == 1
        assert rows[0] == [42, "hello"]

    def test_select_row_count_matches(self, sm):
        mgr, sid = sm
        result = mgr.execute(sid, "SELECT * FROM generate_series(1, 5) AS t(x)")
        assert result["row_count"] == len(result["rows"])
        assert result["row_count"] == 5

    def test_multi_row_select(self, sm):
        mgr, sid = sm
        result = mgr.execute(sid, "SELECT * FROM generate_series(1, 3) AS t(x)")
        assert len(result["rows"]) == 3
        assert result["row_count"] == 3
        values = [row[0] for row in result["rows"]]
        assert values == [1, 2, 3]


class TestSessionCommandResponseStructure:
    """DDL/DML results."""

    def test_command_response_keys(self, sm):
        mgr, sid = sm
        result = mgr.execute(sid, "CREATE TABLE cmd_test (id INT)")
        assert set(result.keys()) == {"status", "columns", "rows", "row_count"}

    def test_command_columns_and_rows(self, sm):
        mgr, sid = sm
        result = mgr.execute(sid, "CREATE TABLE cmd_test2 (id INT)")
        assert result["status"] == "completed"
        # DuckDB returns description for DDL, so columns should be populated
        assert isinstance(result["columns"], list)
        assert isinstance(result["rows"], list)


class TestSessionFetchResultsFalse:
    """fetch_results=False path."""

    def test_no_fetch_response(self, sm):
        mgr, sid = sm
        result = mgr.execute(sid, "SELECT 1 AS x", fetch_results=False)
        assert set(result.keys()) == {"status", "columns", "rows", "row_count"}
        assert result["columns"] is None
        assert result["rows"] is None
        assert isinstance(result["row_count"], int)


class TestSessionErrorResponseStructure:
    """Error response verification."""

    def test_error_response_keys(self, sm):
        mgr, sid = sm
        result = mgr.execute(sid, "SELECT * FROM nonexistent_xyz")
        assert set(result.keys()) == {"status", "error"}

    def test_error_status_value(self, sm):
        mgr, sid = sm
        result = mgr.execute(sid, "SELECT * FROM nonexistent_xyz")
        assert result["status"] == "failed"

    def test_error_message_is_string(self, sm):
        mgr, sid = sm
        result = mgr.execute(sid, "SELECT * FROM nonexistent_xyz")
        assert isinstance(result["error"], str)
        assert len(result["error"]) > 0
