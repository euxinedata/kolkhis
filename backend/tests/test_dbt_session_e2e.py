"""E2E tests for dbt session lifecycle via /api/dbt/* endpoints.

Run: cd backend && uv run pytest tests/test_dbt_session_e2e.py -v
"""

import pytest

from conftest import SCHEMA, dbt_query, safe_cleanup, submit_and_wait

# Realistic dbt comment prefix (dbt prepends this to every query it sends)
DBT_COMMENT = (
    '/* {"app": "dbt", "dbt_version": "1.9.4", "profile_name": "warehouse", '
    '"target_name": "dev", "node_id": "model.warehouse.e2e_model"} */\n'
)


@pytest.fixture(scope="module")
def session_id(api):
    """Create a dbt session shared across this module's tests."""
    resp = api.post("/api/dbt/session")
    resp.raise_for_status()
    data = resp.json()
    yield data["session_id"]
    # Cleanup: close session (ignore errors — test_close_session may have already closed it)
    try:
        api.delete(f"/api/dbt/session/{data['session_id']}")
    except Exception:
        pass


@pytest.fixture(scope="module", autouse=True)
def cleanup_dbt_resources(api):
    """Ensure dbt test resources are removed after all tests, even on failure."""
    yield
    # The dbt session creates these in the _ice_ overlay, but the view/schema
    # are visible through the SQL editor DDL path too. Clean up both paths.
    safe_cleanup(api, "DROP VIEW development.e2e_dbt_test.vw")
    safe_cleanup(api, "DROP TABLE development.e2e_dbt_test.tbl")
    safe_cleanup(api, "DROP SCHEMA development.e2e_dbt_test")
    safe_cleanup(api, f"DROP TABLE development.{SCHEMA}.e2e_dbt_commented")
    safe_cleanup(api, f"DROP VIEW development.{SCHEMA}.e2e_dbt_commented_vw")
    safe_cleanup(api, f"DROP TABLE development.{SCHEMA}.e2e_dbt_comment_dml")


class TestDbtSessionLifecycle:
    """Full dbt session: create -> query -> create table -> create view -> drop -> close."""

    def test_create_session(self, api):
        resp = api.post("/api/dbt/session")
        resp.raise_for_status()
        data = resp.json()
        assert "session_id" in data
        # Close this extra session
        api.delete(f"/api/dbt/session/{data['session_id']}")

    def test_select_in_session(self, api, session_id):
        result = dbt_query(api, session_id, "SELECT 42 AS val")
        assert result["status"] == "completed"
        assert result["rows"] == [[42]]

    def test_select_iceberg_table(self, api, session_id):
        result = dbt_query(
            api, session_id,
            "SELECT count(*) AS cnt FROM retail_catalog.products.brands",
        )
        assert result["status"] == "completed"
        assert result["rows"][0][0] > 0

    def test_create_schema(self, api, session_id):
        result = dbt_query(
            api, session_id, "CREATE SCHEMA development.e2e_dbt_test"
        )
        assert result["status"] == "completed"
        # Verify schema exists in Iceberg via SHOW SCHEMAS
        status, _, results = submit_and_wait(api, "SHOW SCHEMAS IN development")
        assert status == "completed"
        schemas = [r["schema_name"] for r in results["rows"]]
        assert "e2e_dbt_test" in schemas, f"Schema not in Iceberg. Schemas: {schemas}"

    def test_create_table(self, api, session_id):
        result = dbt_query(
            api, session_id,
            "CREATE TABLE development.e2e_dbt_test.tbl AS SELECT 1 AS id, 'x' AS name",
        )
        assert result["status"] == "completed"
        # Verify table landed in Iceberg (catalog API), not just in-memory overlay
        resp = api.get("/api/catalog/databases/development/schemas/e2e_dbt_test/objects")
        resp.raise_for_status()
        objects = resp.json()["objects"]
        names = [o["name"] for o in objects]
        assert "tbl" in names, (
            f"Table not in catalog — likely went to in-memory overlay. Objects: {names}"
        )

    def test_query_created_table(self, api, session_id):
        """Query the table via a separate SQL editor query to prove cross-session persistence."""
        status, job, results = submit_and_wait(
            api, "SELECT * FROM development.e2e_dbt_test.tbl"
        )
        assert status == "completed", f"Query failed: {job.get('error')}"
        rows = results["rows"]
        assert len(rows) == 1
        assert rows[0]["id"] == 1
        assert rows[0]["name"] == "x"

    def test_create_view(self, api, session_id):
        result = dbt_query(
            api, session_id,
            "CREATE VIEW development.e2e_dbt_test.vw AS SELECT id FROM development.e2e_dbt_test.tbl",
        )
        assert result["status"] == "completed"
        # Verify view is stored in catalog (org_views in PostgreSQL)
        resp = api.get("/api/catalog/databases/development/schemas/e2e_dbt_test/objects")
        resp.raise_for_status()
        objects = resp.json()["objects"]
        view = next((o for o in objects if o["name"] == "vw"), None)
        assert view is not None, f"View not in catalog. Objects: {[o['name'] for o in objects]}"
        assert view["type"] == "view"

    def test_query_created_view(self, api, session_id):
        """Query the view via SQL editor to prove it persists across connections."""
        status, job, results = submit_and_wait(
            api, "SELECT * FROM development.e2e_dbt_test.vw"
        )
        assert status == "completed", f"Query failed: {job.get('error')}"
        assert len(results["rows"]) == 1
        assert results["rows"][0]["id"] == 1

    def test_drop_table(self, api, session_id):
        result = dbt_query(
            api, session_id,
            "DROP TABLE development.e2e_dbt_test.tbl",
        )
        assert result["status"] == "completed"
        # Verify table is gone from Iceberg catalog
        resp = api.get("/api/catalog/databases/development/schemas/e2e_dbt_test/objects")
        resp.raise_for_status()
        names = [o["name"] for o in resp.json()["objects"]]
        assert "tbl" not in names, f"Table still in catalog after DROP. Objects: {names}"

    def test_drop_view(self, api, session_id):
        result = dbt_query(
            api, session_id,
            "DROP VIEW development.e2e_dbt_test.vw",
        )
        assert result["status"] == "completed"
        # Verify view is gone from catalog
        resp = api.get("/api/catalog/databases/development/schemas/e2e_dbt_test/objects")
        resp.raise_for_status()
        names = [o["name"] for o in resp.json()["objects"]]
        assert "vw" not in names, f"View still in catalog after DROP. Objects: {names}"

    def test_cleanup_schema(self, api):
        """Use SQL editor to drop schema."""
        status, _, _ = submit_and_wait(
            api, "DROP SCHEMA development.e2e_dbt_test"
        )
        assert status == "completed"

    def test_close_session(self, api, session_id):
        resp = api.delete(f"/api/dbt/session/{session_id}")
        resp.raise_for_status()
        data = resp.json()
        assert data["status"] == "closed"

    def test_query_after_close_fails(self, api, session_id):
        resp = api.post(
            f"/api/dbt/session/{session_id}/query",
            json={"sql": "SELECT 1", "fetch_results": True},
        )
        assert resp.status_code == 404


class TestDbtCommentPrefix:
    """Verify that dbt's /* ... */ comment prefix doesn't break DDL interception.

    dbt prepends a JSON comment to every query. Without stripping it, regex-based
    interception (CREATE TABLE, DROP TABLE, CREATE SCHEMA, etc.) fails because the
    patterns are anchored with ^. This caused materialized tables to go into the
    in-memory overlay instead of Iceberg, making them invisible in the catalog.
    """

    @pytest.fixture(autouse=True)
    def _dbt_session(self, api):
        """Create a fresh dbt session for this class and clean up after."""
        resp = api.post("/api/dbt/session")
        resp.raise_for_status()
        self.session_id = resp.json()["session_id"]
        yield
        try:
            api.delete(f"/api/dbt/session/{self.session_id}")
        except Exception:
            pass

    def test_create_table_with_comment_lands_in_iceberg(self, api):
        """CREATE TABLE with dbt comment prefix must write to Iceberg (_ice_ rewrite)."""
        sql = (
            f'{DBT_COMMENT}'
            f'CREATE TABLE development.{SCHEMA}.e2e_dbt_commented '
            f'AS SELECT 1 AS id'
        )
        result = dbt_query(api, self.session_id, sql)
        assert result["status"] == "completed"

        # The table must be visible in the catalog (Iceberg), not just in-memory
        resp = api.get(f"/api/catalog/databases/development/schemas/{SCHEMA}/objects")
        resp.raise_for_status()
        objects = resp.json()["objects"]
        names = [o["name"] for o in objects]
        assert "e2e_dbt_commented" in names, (
            f"Table not visible in catalog — likely went to in-memory overlay. "
            f"Objects: {names}"
        )

    def test_drop_table_with_comment(self, api):
        """DROP TABLE with dbt comment prefix must target _ice_ prefix."""
        # Create first (with comment)
        dbt_query(
            api, self.session_id,
            f'{DBT_COMMENT}CREATE TABLE development.{SCHEMA}.e2e_dbt_commented '
            f'AS SELECT 1 AS id',
        )
        # Drop with comment
        result = dbt_query(
            api, self.session_id,
            f'{DBT_COMMENT}DROP TABLE development.{SCHEMA}.e2e_dbt_commented',
        )
        assert result["status"] == "completed"

        # Verify it's gone from catalog
        resp = api.get(f"/api/catalog/databases/development/schemas/{SCHEMA}/objects")
        resp.raise_for_status()
        names = [o["name"] for o in resp.json()["objects"]]
        assert "e2e_dbt_commented" not in names

    def test_create_view_with_comment(self, api):
        """CREATE VIEW with dbt comment prefix must be intercepted and stored."""
        sql = (
            f'{DBT_COMMENT}'
            f'CREATE VIEW development.{SCHEMA}.e2e_dbt_commented_vw '
            f'AS SELECT 1 AS val'
        )
        result = dbt_query(api, self.session_id, sql)
        assert result["status"] == "completed"

        # View must appear in catalog
        resp = api.get(f"/api/catalog/databases/development/schemas/{SCHEMA}/objects")
        resp.raise_for_status()
        objects = resp.json()["objects"]
        view = next((o for o in objects if o["name"] == "e2e_dbt_commented_vw"), None)
        assert view is not None, "View not found in catalog"
        assert view["type"] == "view"

        # Cleanup
        dbt_query(
            api, self.session_id,
            f'{DBT_COMMENT}DROP VIEW development.{SCHEMA}.e2e_dbt_commented_vw',
        )

    def test_insert_with_comment(self, api):
        """INSERT with dbt comment prefix works correctly."""
        # Create table first
        dbt_query(
            api, self.session_id,
            f'{DBT_COMMENT}CREATE TABLE development.{SCHEMA}.e2e_dbt_comment_dml '
            f"AS SELECT 1 AS id, 'init' AS val",
        )

        # INSERT with comment
        result = dbt_query(
            api, self.session_id,
            f'{DBT_COMMENT}INSERT INTO development.{SCHEMA}.e2e_dbt_comment_dml '
            f"VALUES (2, 'inserted')",
        )
        assert result["status"] == "completed", f"INSERT with comment failed: {result.get('error')}"

        # Verify
        result = dbt_query(
            api, self.session_id,
            f'SELECT count(*) AS cnt FROM development.{SCHEMA}.e2e_dbt_comment_dml',
        )
        assert result["rows"][0][0] == 2

    def test_update_with_comment(self, api):
        """UPDATE with dbt comment prefix works correctly."""
        result = dbt_query(
            api, self.session_id,
            f"{DBT_COMMENT}UPDATE development.{SCHEMA}.e2e_dbt_comment_dml "
            f"SET val = 'updated' WHERE id = 1",
        )
        assert result["status"] == "completed", f"UPDATE with comment failed: {result.get('error')}"

        result = dbt_query(
            api, self.session_id,
            f"SELECT val FROM development.{SCHEMA}.e2e_dbt_comment_dml WHERE id = 1",
        )
        assert result["rows"][0][0] == "updated"

    def test_delete_with_comment(self, api):
        """DELETE with dbt comment prefix works correctly."""
        result = dbt_query(
            api, self.session_id,
            f"{DBT_COMMENT}DELETE FROM development.{SCHEMA}.e2e_dbt_comment_dml WHERE id = 2",
        )
        assert result["status"] == "completed", f"DELETE with comment failed: {result.get('error')}"

        result = dbt_query(
            api, self.session_id,
            f"SELECT count(*) AS cnt FROM development.{SCHEMA}.e2e_dbt_comment_dml",
        )
        assert result["rows"][0][0] == 1

    def test_merge_with_comment(self, api):
        """MERGE with dbt comment prefix works correctly."""
        result = dbt_query(
            api, self.session_id,
            f"{DBT_COMMENT}MERGE INTO development.{SCHEMA}.e2e_dbt_comment_dml AS target "
            f"USING (SELECT * FROM (VALUES (1, 'merged'), (3, 'new')) AS t(id, val)) AS source "
            f"ON target.id = source.id "
            f"WHEN MATCHED THEN UPDATE SET val = source.val "
            f"WHEN NOT MATCHED THEN INSERT (id, val) VALUES (source.id, source.val)",
        )
        assert result["status"] == "completed", f"MERGE with comment failed: {result.get('error')}"

        result = dbt_query(
            api, self.session_id,
            f"SELECT * FROM development.{SCHEMA}.e2e_dbt_comment_dml ORDER BY id",
        )
        assert len(result["rows"]) == 2
        assert result["rows"][0] == [1, "merged"]
        assert result["rows"][1] == [3, "new"]

        # Cleanup
        dbt_query(
            api, self.session_id,
            f'{DBT_COMMENT}DROP TABLE development.{SCHEMA}.e2e_dbt_comment_dml',
        )
