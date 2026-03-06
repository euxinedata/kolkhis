"""E2E tests for CREATE/DROP DATABASE and SCHEMA full lifecycle.

Run: cd backend && uv run pytest tests/test_database_lifecycle_e2e.py -v
"""

import pytest

from conftest import safe_cleanup, submit_and_wait


@pytest.fixture(scope="module", autouse=True)
def cleanup_e2e_testdb(api):
    """Ensure e2e_testdb is removed after all tests, even on failure."""
    yield
    safe_cleanup(api, "DROP VIEW e2e_testdb.e2e_schema.v")
    safe_cleanup(api, "DROP SCHEMA e2e_testdb.e2e_schema")
    safe_cleanup(api, "DROP DATABASE e2e_testdb")


class TestDatabaseLifecycle:
    """Create DB -> create schema -> create view -> query -> drop everything -> verify gone."""

    def test_create_database(self, api):
        status, data, _ = submit_and_wait(api, "CREATE DATABASE e2e_testdb")
        assert status == "ddl"
        assert "created" in data["ddl_message"].lower()

    def test_show_databases_includes_new(self, api):
        status, job, results = submit_and_wait(api, "SHOW DATABASES")
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        db_names = [r["database_name"] for r in results["rows"]]
        assert "e2e_testdb" in db_names

    def test_create_schema(self, api):
        status, data, _ = submit_and_wait(api, "CREATE SCHEMA e2e_testdb.e2e_schema")
        assert status == "ddl"
        assert "created" in data["ddl_message"].lower()

    def test_show_schemas_includes_new(self, api):
        status, job, results = submit_and_wait(api, "SHOW SCHEMAS IN e2e_testdb")
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        schema_names = [r["schema_name"] for r in results["rows"]]
        assert "e2e_schema" in schema_names

    def test_show_tables_empty_schema(self, api):
        status, job, results = submit_and_wait(api, "SHOW TABLES IN e2e_testdb.e2e_schema")
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert results["total"] == 0

    def test_create_view_in_new_db(self, api):
        status, data, _ = submit_and_wait(
            api, "CREATE VIEW e2e_testdb.e2e_schema.v AS SELECT 1 AS val"
        )
        assert status == "ddl"

    def test_show_tables_includes_view(self, api):
        status, job, results = submit_and_wait(api, "SHOW TABLES IN e2e_testdb.e2e_schema")
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        names = [r["table_name"] for r in results["rows"]]
        types = {r["table_name"]: r["table_type"] for r in results["rows"]}
        assert "v" in names
        assert types["v"] == "VIEW"

    def test_query_view_in_new_db(self, api):
        status, job, results = submit_and_wait(
            api, "SELECT * FROM e2e_testdb.e2e_schema.v"
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert results["rows"] == [{"val": 1}]

    def test_drop_view(self, api):
        status, data, _ = submit_and_wait(api, "DROP VIEW e2e_testdb.e2e_schema.v")
        assert status == "ddl"
        assert "dropped" in data["ddl_message"].lower()

    def test_drop_schema(self, api):
        status, data, _ = submit_and_wait(api, "DROP SCHEMA e2e_testdb.e2e_schema")
        assert status == "ddl"
        assert "dropped" in data["ddl_message"].lower()

    def test_drop_database(self, api):
        status, data, _ = submit_and_wait(api, "DROP DATABASE e2e_testdb")
        assert status == "ddl"
        assert "dropped" in data["ddl_message"].lower()

    def test_show_databases_excludes_dropped(self, api):
        status, job, results = submit_and_wait(api, "SHOW DATABASES")
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        db_names = [r["database_name"] for r in results["rows"]]
        assert "e2e_testdb" not in db_names


class TestDuplicateDatabase:
    """Creating a database that already exists should fail."""

    def test_create_duplicate_database_fails(self, api):
        resp = api.post("/api/queries", json={"sql": "CREATE DATABASE development"})
        assert resp.status_code == 400
        assert "already exists" in resp.json()["detail"].lower()
