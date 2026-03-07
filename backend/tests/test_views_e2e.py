"""End-to-end view tests against running services.

Requires: backend (port 8000), worker (port 8080), PostgreSQL, MinIO.
Tests the actual user flows: CREATE VIEW -> query -> DROP VIEW through the real
API endpoints, hitting real DuckLake storage.

Run: cd backend && uv run pytest tests/test_views_e2e.py -v
"""

import pytest

from conftest import SCHEMA, safe_cleanup, submit_and_wait


@pytest.fixture(autouse=True, scope="class")
def cleanup_lifecycle_views(request, api):
    """Clean up views created by each test class, even on failure."""
    yield
    cls = request.cls
    if cls is TestSqlEditorViewLifecycle:
        safe_cleanup(api, f"DROP VIEW development.{SCHEMA}.e2e_test_view")
    elif cls is TestCrossDatabaseView:
        safe_cleanup(api, f"DROP VIEW development.{SCHEMA}.e2e_cross_db")
    elif cls is TestViewWithRealTableData:
        safe_cleanup(api, f"DROP VIEW development.{SCHEMA}.e2e_brand_count")
    elif cls is TestViewInCatalogSidebar:
        safe_cleanup(api, f"DROP VIEW development.{SCHEMA}.e2e_catalog_test")
        safe_cleanup(api, f"DROP VIEW development.{SCHEMA}.e2e_schema_test")


class TestSqlEditorViewLifecycle:
    """User creates a view, queries it, updates it, drops it -- all through the SQL editor API."""

    def test_create_simple_view(self, api):
        status, data, _ = submit_and_wait(
            api, f"CREATE VIEW development.{SCHEMA}.e2e_test_view AS SELECT 42 AS answer, 'hello' AS greeting"
        )
        assert status == "completed"


    def test_query_simple_view(self, api):
        status, job, results = submit_and_wait(
            api, f"SELECT * FROM development.{SCHEMA}.e2e_test_view"
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert results["columns"] == ["answer", "greeting"]
        assert results["rows"] == [{"answer": 42, "greeting": "hello"}]

    def test_create_or_replace_view(self, api):
        status, data, _ = submit_and_wait(
            api, f"CREATE OR REPLACE VIEW development.{SCHEMA}.e2e_test_view AS SELECT 99 AS answer"
        )
        assert status == "completed"

    def test_query_replaced_view(self, api):
        status, job, results = submit_and_wait(
            api, f"SELECT * FROM development.{SCHEMA}.e2e_test_view"
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert results["columns"] == ["answer"]
        assert results["rows"] == [{"answer": 99}]

    def test_drop_view(self, api):
        status, data, _ = submit_and_wait(
            api, f"DROP VIEW development.{SCHEMA}.e2e_test_view"
        )
        assert status == "completed"


    def test_query_dropped_view_fails(self, api):
        status, job, _ = submit_and_wait(
            api, f"SELECT * FROM development.{SCHEMA}.e2e_test_view"
        )
        assert status == "failed"
        assert "e2e_test_view" in job["error"]


class TestCrossDatabaseView:
    """View in development referencing a table in another Iceberg database."""

    def test_create_cross_db_view(self, api):
        status, data, _ = submit_and_wait(
            api,
            f"CREATE VIEW development.{SCHEMA}.e2e_cross_db AS "
            "SELECT customer_id, tier FROM retail_sales.customers.loyalty_accounts LIMIT 5",
        )
        assert status == "completed"

    def test_query_cross_db_view(self, api):
        status, job, results = submit_and_wait(
            api, f"SELECT * FROM development.{SCHEMA}.e2e_cross_db"
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert "customer_id" in results["columns"]
        assert "tier" in results["columns"]
        assert results["total"] == 5

    def test_cleanup_cross_db_view(self, api):
        status, _, _ = submit_and_wait(
            api, f"DROP VIEW development.{SCHEMA}.e2e_cross_db"
        )
        assert status == "completed"


class TestViewWithRealTableData:
    """View referencing an actual Iceberg table with real data."""

    def test_create_aggregation_view(self, api):
        status, data, _ = submit_and_wait(
            api,
            f"CREATE VIEW development.{SCHEMA}.e2e_brand_count AS "
            "SELECT count(*) AS cnt FROM retail_catalog.products.brands",
        )
        assert status == "completed"

    def test_query_aggregation_view(self, api):
        status, job, results = submit_and_wait(
            api, f"SELECT * FROM development.{SCHEMA}.e2e_brand_count"
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert results["columns"] == ["cnt"]
        assert results["rows"][0]["cnt"] > 0

    def test_cleanup_aggregation_view(self, api):
        status, _, _ = submit_and_wait(
            api, f"DROP VIEW development.{SCHEMA}.e2e_brand_count"
        )
        assert status == "completed"


class TestViewInCatalogSidebar:
    """Views should appear in the catalog API."""

    def test_view_appears_in_objects_list(self, api):
        submit_and_wait(
            api,
            f"CREATE VIEW development.{SCHEMA}.e2e_catalog_test AS SELECT 1 AS x",
        )

        resp = api.get(f"/api/catalog/databases/development/schemas/{SCHEMA}/objects")
        resp.raise_for_status()
        objects = resp.json()["objects"]
        view_names = [o["name"] for o in objects if o["type"] == "view"]
        assert "e2e_catalog_test" in view_names

        submit_and_wait(
            api, f"DROP VIEW development.{SCHEMA}.e2e_catalog_test"
        )

    def test_view_schema_endpoint(self, api):
        submit_and_wait(
            api,
            f"CREATE VIEW development.{SCHEMA}.e2e_schema_test AS SELECT 1 AS x",
        )

        resp = api.get(
            f"/api/catalog/databases/development/schemas/{SCHEMA}/objects/e2e_schema_test/schema"
        )
        resp.raise_for_status()
        data = resp.json()
        assert data["type"] == "view"
        assert "SELECT 1 AS x" in data["view_sql"]

        submit_and_wait(
            api, f"DROP VIEW development.{SCHEMA}.e2e_schema_test"
        )


class TestViewDependency:
    """View dependency behavior when the underlying table is dropped."""

    @pytest.fixture(autouse=True)
    def _cleanup(self, api):
        yield
        safe_cleanup(api, f"DROP VIEW development.{SCHEMA}.e2e_dep_view")
        safe_cleanup(api, f"DROP TABLE development.{SCHEMA}.e2e_dep_tbl")

    def test_drop_table_with_dependent_view(self, api):
        """DROP TABLE succeeds even when a view depends on it."""
        submit_and_wait(
            api,
            f"CREATE TABLE development.{SCHEMA}.e2e_dep_tbl AS SELECT 1 AS id, 'x' AS val",
        )
        submit_and_wait(
            api,
            f"CREATE VIEW development.{SCHEMA}.e2e_dep_view "
            f"AS SELECT id FROM development.{SCHEMA}.e2e_dep_tbl",
        )

        # DuckLake allows dropping the table without CASCADE
        status, job, _ = submit_and_wait(
            api, f"DROP TABLE development.{SCHEMA}.e2e_dep_tbl"
        )
        assert status == "completed", f"DROP TABLE failed: {job.get('error')}"

    def test_orphan_view_query_fails(self, api):
        """Querying a view whose base table was dropped gives a clear error."""
        submit_and_wait(
            api,
            f"CREATE TABLE development.{SCHEMA}.e2e_dep_tbl AS SELECT 1 AS id",
        )
        submit_and_wait(
            api,
            f"CREATE VIEW development.{SCHEMA}.e2e_dep_view "
            f"AS SELECT id FROM development.{SCHEMA}.e2e_dep_tbl",
        )
        submit_and_wait(
            api, f"DROP TABLE development.{SCHEMA}.e2e_dep_tbl"
        )

        # Querying the orphan view should fail
        status, job, _ = submit_and_wait(
            api, f"SELECT * FROM development.{SCHEMA}.e2e_dep_view"
        )
        assert status == "failed", "Orphan view query should fail"
        assert "e2e_dep_tbl" in job["error"], (
            f"Error should mention missing table: {job['error']}"
        )

    def test_orphan_view_still_in_catalog(self, api):
        """An orphaned view still appears in the catalog (not silently removed)."""
        submit_and_wait(
            api,
            f"CREATE TABLE development.{SCHEMA}.e2e_dep_tbl AS SELECT 1 AS id",
        )
        submit_and_wait(
            api,
            f"CREATE VIEW development.{SCHEMA}.e2e_dep_view "
            f"AS SELECT id FROM development.{SCHEMA}.e2e_dep_tbl",
        )
        submit_and_wait(
            api, f"DROP TABLE development.{SCHEMA}.e2e_dep_tbl"
        )

        # View should still be listed in catalog
        resp = api.get(
            f"/api/catalog/databases/development/schemas/{SCHEMA}/objects"
        )
        resp.raise_for_status()
        names = [o["name"] for o in resp.json()["objects"]]
        assert "e2e_dep_view" in names, (
            f"Orphan view disappeared from catalog: {names}"
        )
