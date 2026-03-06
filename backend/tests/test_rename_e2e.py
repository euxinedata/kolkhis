"""E2E tests for ALTER ... RENAME TO operations on all database entities.

Run: cd backend && uv run pytest tests/test_rename_e2e.py -v

Each test class creates resources, renames them, and verifies via:
- Catalog API (proves rename landed in Lakekeeper/PostgreSQL, not just in-memory)
- Cross-connection queries (proves worker can see the new name)
- SHOW commands (proves the old name is gone)
"""

import pytest

from conftest import SCHEMA, safe_cleanup, submit_and_wait


# ---------------------------------------------------------------------------
# RENAME VIEW
# ---------------------------------------------------------------------------
class TestRenameView:
    """ALTER VIEW db.schema.old RENAME TO new_name"""

    @pytest.fixture(autouse=True)
    def _cleanup(self, api):
        yield
        safe_cleanup(api, f"DROP VIEW development.{SCHEMA}.e2e_rv_original")
        safe_cleanup(api, f"DROP VIEW development.{SCHEMA}.e2e_rv_renamed")

    def test_rename_view(self, api):
        # Create a view
        status, data, _ = submit_and_wait(
            api,
            f"CREATE VIEW development.{SCHEMA}.e2e_rv_original AS SELECT 42 AS val",
        )
        assert status == "ddl"

        # Rename it
        status, data, _ = submit_and_wait(
            api,
            f"ALTER VIEW development.{SCHEMA}.e2e_rv_original RENAME TO e2e_rv_renamed",
        )
        assert status == "ddl"
        assert "renamed" in data["ddl_message"].lower()

        # Verify new name exists in catalog
        resp = api.get(
            f"/api/catalog/databases/development/schemas/{SCHEMA}/objects"
        )
        resp.raise_for_status()
        names = [o["name"] for o in resp.json()["objects"]]
        assert "e2e_rv_renamed" in names, f"Renamed view not in catalog. Objects: {names}"
        assert "e2e_rv_original" not in names, f"Old view name still in catalog. Objects: {names}"

        # Verify queryable under new name (cross-connection via SQL editor)
        status, job, results = submit_and_wait(
            api, f"SELECT * FROM development.{SCHEMA}.e2e_rv_renamed"
        )
        assert status == "completed", f"Query failed: {job.get('error')}"
        assert results["rows"][0]["val"] == 42

    def test_rename_view_old_name_gone(self, api):
        """Query under old name must fail after rename."""
        submit_and_wait(
            api,
            f"CREATE VIEW development.{SCHEMA}.e2e_rv_original AS SELECT 1 AS x",
        )
        submit_and_wait(
            api,
            f"ALTER VIEW development.{SCHEMA}.e2e_rv_original RENAME TO e2e_rv_renamed",
        )

        # Old name should not be queryable
        status, job, _ = submit_and_wait(
            api, f"SELECT * FROM development.{SCHEMA}.e2e_rv_original"
        )
        assert status == "failed", "Old view name should not be queryable after rename"

    def test_rename_nonexistent_view_fails(self, api):
        resp = api.post(
            "/api/queries",
            json={"sql": f"ALTER VIEW development.{SCHEMA}.e2e_no_such_view RENAME TO x"},
        )
        assert resp.status_code == 400
        assert "not found" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# RENAME TABLE
# ---------------------------------------------------------------------------
class TestRenameTable:
    """ALTER TABLE db.schema.old RENAME TO new_name

    Table rename uses PyIceberg rename_table — must be visible in Iceberg catalog.
    """

    @pytest.fixture(autouse=True)
    def _cleanup(self, api):
        yield
        safe_cleanup(api, f"DROP TABLE development.{SCHEMA}.e2e_rt_original")
        safe_cleanup(api, f"DROP TABLE development.{SCHEMA}.e2e_rt_renamed")

    @pytest.fixture(autouse=True)
    def _dbt_session(self, api):
        """Create a dbt session for table creation (needs _ice_ rewrite)."""
        resp = api.post("/api/dbt/session")
        resp.raise_for_status()
        self.session_id = resp.json()["session_id"]
        yield
        try:
            api.delete(f"/api/dbt/session/{self.session_id}")
        except Exception:
            pass

    def test_rename_table(self, api):
        from conftest import dbt_query

        # Create table via dbt session (lands in Iceberg)
        result = dbt_query(
            api, self.session_id,
            f"CREATE TABLE development.{SCHEMA}.e2e_rt_original AS SELECT 1 AS id, 'hello' AS msg",
        )
        assert result["status"] == "completed"

        # Verify it exists in catalog before rename
        resp = api.get(f"/api/catalog/databases/development/schemas/{SCHEMA}/objects")
        resp.raise_for_status()
        names = [o["name"] for o in resp.json()["objects"]]
        assert "e2e_rt_original" in names

        # Rename via SQL editor
        status, data, _ = submit_and_wait(
            api,
            f"ALTER TABLE development.{SCHEMA}.e2e_rt_original RENAME TO e2e_rt_renamed",
        )
        assert status == "ddl"
        assert "renamed" in data["ddl_message"].lower()

        # Verify new name in Iceberg catalog
        resp = api.get(f"/api/catalog/databases/development/schemas/{SCHEMA}/objects")
        resp.raise_for_status()
        names = [o["name"] for o in resp.json()["objects"]]
        assert "e2e_rt_renamed" in names, f"Renamed table not in catalog. Objects: {names}"
        assert "e2e_rt_original" not in names, f"Old table still in catalog. Objects: {names}"

        # Verify queryable under new name (cross-connection)
        status, job, results = submit_and_wait(
            api, f"SELECT * FROM development.{SCHEMA}.e2e_rt_renamed"
        )
        assert status == "completed", f"Query failed: {job.get('error')}"
        assert len(results["rows"]) == 1
        assert results["rows"][0]["id"] == 1

    def test_rename_nonexistent_table_fails(self, api):
        resp = api.post(
            "/api/queries",
            json={"sql": f"ALTER TABLE development.{SCHEMA}.e2e_no_such_tbl RENAME TO x"},
        )
        assert resp.status_code == 400
        assert "not found" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# RENAME SCHEMA
# ---------------------------------------------------------------------------
class TestRenameSchema:
    """ALTER SCHEMA db.old_name RENAME TO new_name

    Schema rename requires: create new namespace, move all tables + views, drop old.
    """

    @pytest.fixture(autouse=True)
    def _cleanup(self, api):
        yield
        # Clean up both possible schema names and their contents
        safe_cleanup(api, "DROP VIEW development.e2e_rs_original.v1")
        safe_cleanup(api, "DROP VIEW development.e2e_rs_renamed.v1")
        safe_cleanup(api, "DROP TABLE development.e2e_rs_original.t1")
        safe_cleanup(api, "DROP TABLE development.e2e_rs_renamed.t1")
        safe_cleanup(api, "DROP SCHEMA development.e2e_rs_original")
        safe_cleanup(api, "DROP SCHEMA development.e2e_rs_renamed")

    @pytest.fixture(autouse=True)
    def _dbt_session(self, api):
        """Create a dbt session for table creation."""
        resp = api.post("/api/dbt/session")
        resp.raise_for_status()
        self.session_id = resp.json()["session_id"]
        yield
        try:
            api.delete(f"/api/dbt/session/{self.session_id}")
        except Exception:
            pass

    def test_rename_schema_with_contents(self, api):
        from conftest import dbt_query

        # Create schema with a table and a view
        submit_and_wait(api, "CREATE SCHEMA development.e2e_rs_original")
        dbt_query(
            api, self.session_id,
            "CREATE TABLE development.e2e_rs_original.t1 AS SELECT 1 AS id",
        )
        submit_and_wait(
            api,
            "CREATE VIEW development.e2e_rs_original.v1 AS SELECT 1 AS val",
        )

        # Verify contents exist before rename
        resp = api.get("/api/catalog/databases/development/schemas/e2e_rs_original/objects")
        resp.raise_for_status()
        before_names = [o["name"] for o in resp.json()["objects"]]
        assert "t1" in before_names
        assert "v1" in before_names

        # Rename the schema
        status, data, _ = submit_and_wait(
            api,
            "ALTER SCHEMA development.e2e_rs_original RENAME TO e2e_rs_renamed",
        )
        assert status == "ddl"
        assert "renamed" in data["ddl_message"].lower()

        # Verify new schema appears in SHOW SCHEMAS
        status, job, results = submit_and_wait(api, "SHOW SCHEMAS IN development")
        assert status == "completed"
        schema_names = [r["schema_name"] for r in results["rows"]]
        assert "e2e_rs_renamed" in schema_names, f"New schema not in SHOW SCHEMAS: {schema_names}"
        assert "e2e_rs_original" not in schema_names, f"Old schema still in SHOW SCHEMAS: {schema_names}"

        # Verify all objects moved to new schema
        resp = api.get("/api/catalog/databases/development/schemas/e2e_rs_renamed/objects")
        resp.raise_for_status()
        after_names = [o["name"] for o in resp.json()["objects"]]
        assert "t1" in after_names, f"Table not moved. Objects: {after_names}"
        assert "v1" in after_names, f"View not moved. Objects: {after_names}"

        # Verify table queryable under new schema (cross-connection)
        status, job, results = submit_and_wait(
            api, "SELECT * FROM development.e2e_rs_renamed.t1"
        )
        assert status == "completed", f"Query failed: {job.get('error')}"
        assert results["rows"][0]["id"] == 1

    def test_rename_empty_schema(self, api):
        submit_and_wait(api, "CREATE SCHEMA development.e2e_rs_original")

        status, data, _ = submit_and_wait(
            api,
            "ALTER SCHEMA development.e2e_rs_original RENAME TO e2e_rs_renamed",
        )
        assert status == "ddl"

        status, job, results = submit_and_wait(api, "SHOW SCHEMAS IN development")
        schema_names = [r["schema_name"] for r in results["rows"]]
        assert "e2e_rs_renamed" in schema_names
        assert "e2e_rs_original" not in schema_names

    def test_rename_nonexistent_schema_fails(self, api):
        resp = api.post(
            "/api/queries",
            json={"sql": "ALTER SCHEMA development.e2e_no_such_schema RENAME TO x"},
        )
        assert resp.status_code == 400
        assert "not found" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# RENAME DATABASE
# ---------------------------------------------------------------------------
class TestRenameDatabase:
    """ALTER DATABASE old_name RENAME TO new_name

    Database rename uses Lakekeeper warehouse rename + OrgDatabase update.
    """

    @pytest.fixture(autouse=True)
    def _cleanup(self, api):
        yield
        safe_cleanup(api, "DROP VIEW e2e_rd_original.e2e_s.v1")
        safe_cleanup(api, "DROP VIEW e2e_rd_renamed.e2e_s.v1")
        safe_cleanup(api, "DROP SCHEMA e2e_rd_original.e2e_s")
        safe_cleanup(api, "DROP SCHEMA e2e_rd_renamed.e2e_s")
        safe_cleanup(api, "DROP DATABASE e2e_rd_original")
        safe_cleanup(api, "DROP DATABASE e2e_rd_renamed")

    def test_rename_database(self, api):
        # Create database with schema and view
        submit_and_wait(api, "CREATE DATABASE e2e_rd_original")
        submit_and_wait(api, "CREATE SCHEMA e2e_rd_original.e2e_s")
        submit_and_wait(
            api,
            "CREATE VIEW e2e_rd_original.e2e_s.v1 AS SELECT 99 AS val",
        )

        # Verify database exists
        status, job, results = submit_and_wait(api, "SHOW DATABASES")
        db_names = [r["database_name"] for r in results["rows"]]
        assert "e2e_rd_original" in db_names

        # Rename it
        status, data, _ = submit_and_wait(
            api, "ALTER DATABASE e2e_rd_original RENAME TO e2e_rd_renamed"
        )
        assert status == "ddl"
        assert "renamed" in data["ddl_message"].lower()

        # Verify new name in SHOW DATABASES, old name gone
        status, job, results = submit_and_wait(api, "SHOW DATABASES")
        db_names = [r["database_name"] for r in results["rows"]]
        assert "e2e_rd_renamed" in db_names, f"New DB not in SHOW DATABASES: {db_names}"
        assert "e2e_rd_original" not in db_names, f"Old DB still in SHOW DATABASES: {db_names}"

        # Verify schema survived the rename
        status, job, results = submit_and_wait(api, "SHOW SCHEMAS IN e2e_rd_renamed")
        assert status == "completed"
        schema_names = [r["schema_name"] for r in results["rows"]]
        assert "e2e_s" in schema_names, f"Schema not found after DB rename: {schema_names}"

        # Verify view queryable under new database name (cross-connection)
        status, job, results = submit_and_wait(
            api, "SELECT * FROM e2e_rd_renamed.e2e_s.v1"
        )
        assert status == "completed", f"Query failed: {job.get('error')}"
        assert results["rows"][0]["val"] == 99

    def test_rename_nonexistent_database_fails(self, api):
        resp = api.post(
            "/api/queries",
            json={"sql": "ALTER DATABASE e2e_no_such_db RENAME TO x"},
        )
        assert resp.status_code == 400
        assert "not found" in resp.json()["detail"].lower()

    def test_rename_to_existing_name_fails(self, api):
        """Can't rename to a database name that already exists."""
        resp = api.post(
            "/api/queries",
            json={"sql": "ALTER DATABASE development RENAME TO retail_catalog"},
        )
        assert resp.status_code == 400
        assert "already exists" in resp.json()["detail"].lower()
