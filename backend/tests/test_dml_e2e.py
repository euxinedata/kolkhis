"""E2E tests for DML operations needed by dbt incremental models, seeds, and snapshots.

Run: cd backend && uv run pytest tests/test_dml_e2e.py -v

These tests verify that INSERT INTO, DELETE FROM, and UPDATE work correctly
through the dbt session proxy against DuckLake tables. Each test creates
resources, performs DML, and verifies via:
- Catalog API (proves data landed in DuckLake)
- Cross-connection queries (proves persistence across sessions)
"""

import pytest

from conftest import SCHEMA, dbt_query, safe_cleanup, submit_and_wait


@pytest.fixture(scope="module")
def dbt_session(api):
    """Create a dbt session shared across this module's tests."""
    resp = api.post("/api/dbt/session")
    resp.raise_for_status()
    session_id = resp.json()["session_id"]
    yield session_id
    try:
        api.delete(f"/api/dbt/session/{session_id}")
    except Exception:
        pass


@pytest.fixture(scope="module", autouse=True)
def cleanup_dml_resources(api):
    """Ensure DML test resources are removed after all tests."""
    yield
    safe_cleanup(api, f"DROP TABLE development.{SCHEMA}.e2e_dml_target")
    safe_cleanup(api, f"DROP TABLE development.{SCHEMA}.e2e_dml_tmp")
    safe_cleanup(api, f"DROP TABLE development.{SCHEMA}.e2e_seed")
    safe_cleanup(api, f"DROP TABLE development.{SCHEMA}.e2e_snapshot")
    safe_cleanup(api, f"DROP TABLE development.{SCHEMA}.e2e_delete_target")
    safe_cleanup(api, f"DROP TABLE development.{SCHEMA}.e2e_update_target")


# ---------------------------------------------------------------------------
# INSERT INTO (incremental append pattern)
# ---------------------------------------------------------------------------
class TestInsertInto:
    """INSERT INTO db.schema.table SELECT ... FROM db.schema.tmp

    This is the core pattern for dbt incremental models with append strategy:
    1. CREATE TABLE target AS SELECT ... (first run)
    2. CREATE TABLE __dbt_tmp AS SELECT ... (staging)
    3. INSERT INTO target SELECT * FROM __dbt_tmp (append)
    4. DROP TABLE __dbt_tmp
    """

    def test_incremental_append_pattern(self, api, dbt_session):
        # Step 1: Create target table (first run — CTAS)
        result = dbt_query(
            api, dbt_session,
            f'CREATE TABLE development."{SCHEMA}".e2e_dml_target '
            f"AS SELECT 1 AS id, 'first' AS batch",
        )
        assert result["status"] == "completed"

        # Verify table in catalog
        resp = api.get(f"/api/catalog/databases/development/schemas/{SCHEMA}/objects")
        resp.raise_for_status()
        names = [o["name"] for o in resp.json()["objects"]]
        assert "e2e_dml_target" in names

        # Step 2: Create staging table (dbt __dbt_tmp)
        result = dbt_query(
            api, dbt_session,
            f'CREATE TABLE development."{SCHEMA}".e2e_dml_tmp '
            f"AS SELECT 2 AS id, 'second' AS batch",
        )
        assert result["status"] == "completed"

        # Step 3: INSERT INTO target from staging
        result = dbt_query(
            api, dbt_session,
            f'INSERT INTO development."{SCHEMA}".e2e_dml_target '
            f'SELECT * FROM development."{SCHEMA}".e2e_dml_tmp',
        )
        assert result["status"] == "completed", f"INSERT failed: {result.get('error')}"

        # Step 4: Drop staging table
        result = dbt_query(
            api, dbt_session,
            f'DROP TABLE development."{SCHEMA}".e2e_dml_tmp',
        )
        assert result["status"] == "completed"

        # Verify: query target via SQL editor (cross-connection) to prove persistence
        status, job, results = submit_and_wait(
            api, f'SELECT * FROM development."{SCHEMA}".e2e_dml_target ORDER BY id'
        )
        assert status == "completed", f"Query failed: {job.get('error')}"
        assert len(results["rows"]) == 2, f"Expected 2 rows after INSERT, got {len(results['rows'])}"
        assert results["rows"][0]["id"] == 1
        assert results["rows"][1]["id"] == 2

    def test_insert_values(self, api, dbt_session):
        """INSERT INTO ... VALUES (...) — used by dbt seed."""
        # Create table first
        result = dbt_query(
            api, dbt_session,
            f'CREATE TABLE development."{SCHEMA}".e2e_seed '
            f"AS SELECT 0 AS id, '' AS name WHERE false",
        )
        assert result["status"] == "completed"

        # Insert values (dbt seed pattern — batched inserts)
        result = dbt_query(
            api, dbt_session,
            f"INSERT INTO development.\"{SCHEMA}\".e2e_seed VALUES (1, 'alpha'), (2, 'beta')",
        )
        assert result["status"] == "completed", f"INSERT VALUES failed: {result.get('error')}"

        # Verify via cross-connection
        status, job, results = submit_and_wait(
            api, f'SELECT * FROM development."{SCHEMA}".e2e_seed ORDER BY id'
        )
        assert status == "completed", f"Query failed: {job.get('error')}"
        assert len(results["rows"]) == 2
        assert results["rows"][0]["name"] == "alpha"


# ---------------------------------------------------------------------------
# DELETE FROM (incremental delete+insert pattern)
# ---------------------------------------------------------------------------
class TestDeleteFrom:
    """DELETE FROM db.schema.table WHERE ...

    Used by dbt incremental models with delete+insert strategy:
    1. CREATE TABLE __dbt_tmp AS SELECT ... (new rows)
    2. DELETE FROM target WHERE key IN (SELECT key FROM __dbt_tmp)
    3. INSERT INTO target SELECT * FROM __dbt_tmp
    4. DROP TABLE __dbt_tmp
    """

    def test_delete_insert_pattern(self, api, dbt_session):
        # Create target with initial data
        result = dbt_query(
            api, dbt_session,
            f'CREATE TABLE development."{SCHEMA}".e2e_delete_target '
            f"AS SELECT * FROM (VALUES (1, 'old_a'), (2, 'old_b'), (3, 'keep')) AS t(id, val)",
        )
        assert result["status"] == "completed"

        # Create staging with updated rows for ids 1 and 2
        result = dbt_query(
            api, dbt_session,
            f'CREATE TABLE development."{SCHEMA}".e2e_dml_tmp '
            f"AS SELECT * FROM (VALUES (1, 'new_a'), (2, 'new_b')) AS t(id, val)",
        )
        assert result["status"] == "completed"

        # DELETE matching rows from target
        result = dbt_query(
            api, dbt_session,
            f'DELETE FROM development."{SCHEMA}".e2e_delete_target '
            f'WHERE id IN (SELECT id FROM development."{SCHEMA}".e2e_dml_tmp)',
        )
        assert result["status"] == "completed", f"DELETE failed: {result.get('error')}"

        # INSERT updated rows
        result = dbt_query(
            api, dbt_session,
            f'INSERT INTO development."{SCHEMA}".e2e_delete_target '
            f'SELECT * FROM development."{SCHEMA}".e2e_dml_tmp',
        )
        assert result["status"] == "completed", f"INSERT failed: {result.get('error')}"

        # Drop staging
        dbt_query(
            api, dbt_session,
            f'DROP TABLE development."{SCHEMA}".e2e_dml_tmp',
        )

        # Verify via cross-connection: 3 rows, ids 1+2 updated, id 3 kept
        status, job, results = submit_and_wait(
            api,
            f'SELECT * FROM development."{SCHEMA}".e2e_delete_target ORDER BY id',
        )
        assert status == "completed", f"Query failed: {job.get('error')}"
        assert len(results["rows"]) == 3
        rows_by_id = {r["id"]: r["val"] for r in results["rows"]}
        assert rows_by_id[1] == "new_a", f"Row 1 not updated: {rows_by_id}"
        assert rows_by_id[2] == "new_b", f"Row 2 not updated: {rows_by_id}"
        assert rows_by_id[3] == "keep", f"Row 3 should be unchanged: {rows_by_id}"


# ---------------------------------------------------------------------------
# UPDATE (snapshot pattern)
# ---------------------------------------------------------------------------
class TestUpdate:
    """UPDATE db.schema.table SET ... WHERE ...

    Used by dbt snapshots (SCD Type 2) to close out old records.
    """

    def test_update_rows(self, api, dbt_session):
        # Create snapshot-like table
        result = dbt_query(
            api, dbt_session,
            f'CREATE TABLE development."{SCHEMA}".e2e_update_target AS '
            f"SELECT * FROM (VALUES "
            f"(1, 'active', CAST(NULL AS VARCHAR)), "
            f"(2, 'active', CAST(NULL AS VARCHAR))"
            f") AS t(id, status, closed_at)",
        )
        assert result["status"] == "completed"

        # UPDATE to close record (snapshot pattern)
        result = dbt_query(
            api, dbt_session,
            f'UPDATE development."{SCHEMA}".e2e_update_target '
            f"SET status = 'closed', closed_at = '2026-01-01' "
            f"WHERE id = 1",
        )
        assert result["status"] == "completed", f"UPDATE failed: {result.get('error')}"

        # Verify via cross-connection
        status, job, results = submit_and_wait(
            api,
            f'SELECT * FROM development."{SCHEMA}".e2e_update_target ORDER BY id',
        )
        assert status == "completed", f"Query failed: {job.get('error')}"
        assert len(results["rows"]) == 2
        rows_by_id = {r["id"]: r for r in results["rows"]}
        assert rows_by_id[1]["status"] == "closed"
        assert rows_by_id[2]["status"] == "active"


# ---------------------------------------------------------------------------
# Bare CREATE TABLE (seed pattern)
# ---------------------------------------------------------------------------
class TestBareCreateTable:
    """CREATE TABLE db.schema.name (col1 TYPE, col2 TYPE, ...)

    Used by dbt seed — creates table with schema only (no data), then INSERTs.
    Unlike CTAS, this has column definitions instead of AS SELECT.
    """

    @pytest.fixture(autouse=True)
    def _cleanup(self, api):
        yield
        safe_cleanup(api, f"DROP TABLE development.{SCHEMA}.e2e_bare_tbl")

    def test_bare_create_table(self, api):
        """dbt session should handle bare CREATE TABLE for seeds."""
        resp = api.post("/api/dbt/session")
        resp.raise_for_status()
        sid = resp.json()["session_id"]
        try:
            result = dbt_query(
                api, sid,
                f'CREATE TABLE development."{SCHEMA}".e2e_bare_tbl '
                f"(id INTEGER, name VARCHAR)",
            )
            assert result["status"] == "completed", (
                f"Bare CREATE TABLE failed: {result.get('error')}"
            )

            # Verify in catalog
            resp = api.get(
                f"/api/catalog/databases/development/schemas/{SCHEMA}/objects"
            )
            resp.raise_for_status()
            names = [o["name"] for o in resp.json()["objects"]]
            assert "e2e_bare_tbl" in names, (
                f"Bare CREATE TABLE not in catalog. Objects: {names}"
            )
        finally:
            api.delete(f"/api/dbt/session/{sid}")


# ---------------------------------------------------------------------------
# DROP SCHEMA CASCADE (dbt cleanup pattern)
# ---------------------------------------------------------------------------
class TestDropSchemaCascade:
    """DROP SCHEMA IF EXISTS db.schema CASCADE

    dbt sends CASCADE on schema drop. DuckLake handles this natively.
    """

    @pytest.fixture(autouse=True)
    def _cleanup(self, api):
        yield
        safe_cleanup(api, "DROP VIEW development.e2e_cascade_schema.v")
        safe_cleanup(api, "DROP SCHEMA development.e2e_cascade_schema")

    def test_drop_schema_cascade(self, api):
        # Create schema with a view
        submit_and_wait(api, "CREATE SCHEMA development.e2e_cascade_schema")
        submit_and_wait(
            api,
            "CREATE OR REPLACE VIEW development.e2e_cascade_schema.v AS SELECT 1 AS x",
        )

        # Drop with CASCADE (dbt pattern) — flows to DuckLake worker
        status, data, _ = submit_and_wait(
            api, "DROP SCHEMA development.e2e_cascade_schema CASCADE"
        )
        assert status == "completed", (
            f"DROP SCHEMA CASCADE failed. Got status={status}, data={data}"
        )

        # Verify schema is gone
        status, job, results = submit_and_wait(api, "SHOW SCHEMAS IN development")
        schema_names = [r["schema_name"] for r in results["rows"]]
        assert "e2e_cascade_schema" not in schema_names
