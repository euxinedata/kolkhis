"""E2E tests for error handling -- bad SQL, bad identifiers, nonexistent resources.

Run: cd backend && uv run pytest tests/test_error_handling_e2e.py -v
"""

from conftest import SCHEMA, safe_cleanup, submit_and_wait


class TestInvalidSql:
    """Invalid SQL should return failed status."""

    def test_invalid_sql(self, api):
        status, job, _ = submit_and_wait(api, "SELCT * FORM nothing")
        assert status == "failed"

    def test_nonexistent_table(self, api):
        status, job, _ = submit_and_wait(
            api, "SELECT * FROM development.nonexistent.nothing"
        )
        assert status == "failed"
        assert "nothing" in job["error"].lower() or "nonexistent" in job["error"].lower()


class TestSingleQuotedIdentifiers:
    """Single-quoted identifiers should be rejected with HTTP 400."""

    def test_single_quoted_database(self, api):
        resp = api.post("/api/queries", json={"sql": "CREATE DATABASE 'bad'"})
        assert resp.status_code == 400
        assert "single quotes" in resp.json()["detail"].lower()

    def test_single_quoted_schema(self, api):
        resp = api.post("/api/queries", json={"sql": "CREATE SCHEMA 'a'.'b'"})
        assert resp.status_code == 400
        assert "single quotes" in resp.json()["detail"].lower()

    def test_single_quoted_drop(self, api):
        resp = api.post("/api/queries", json={"sql": "DROP TABLE 'a'.'b'.'c'"})
        assert resp.status_code == 400
        assert "single quotes" in resp.json()["detail"].lower()


class TestNonexistentResources:
    """Operations on nonexistent databases/views should fail."""

    def test_drop_nonexistent_database(self, api):
        resp = api.post("/api/queries", json={"sql": "DROP DATABASE e2e_nonexistent"})
        assert resp.status_code == 400
        assert "not found" in resp.json()["detail"].lower()

    def test_drop_nonexistent_view(self, api):
        """DROP VIEW on nonexistent view fails at DuckLake level."""
        status, job, _ = submit_and_wait(
            api,
            f"DROP VIEW development.{SCHEMA}.e2e_nonexistent",
        )
        assert status == "failed"

    def test_create_view_in_nonexistent_db(self, api):
        """CREATE VIEW in nonexistent database fails at worker level."""
        status, job, _ = submit_and_wait(
            api, "CREATE VIEW nonexistent.s.v AS SELECT 1",
        )
        assert status == "failed"


class TestDuplicateView:
    """Creating a duplicate view (without OR REPLACE) should fail."""

    def test_create_duplicate_view(self, api):
        try:
            submit_and_wait(
                api,
                f"CREATE VIEW development.{SCHEMA}.e2e_dup_test AS SELECT 1 AS x",
            )

            status, job, _ = submit_and_wait(
                api,
                f"CREATE VIEW development.{SCHEMA}.e2e_dup_test AS SELECT 2 AS y",
            )
            assert status == "failed"
        finally:
            safe_cleanup(api, f"DROP VIEW development.{SCHEMA}.e2e_dup_test")
