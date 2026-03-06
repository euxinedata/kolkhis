"""E2E tests for regular SELECT queries through the worker against real Iceberg tables.

Run: cd backend && uv run pytest tests/test_queries_e2e.py -v
"""

from conftest import SCHEMA, safe_cleanup, submit_and_wait


class TestSimpleQueries:
    """Basic SELECT queries against real Iceberg tables."""

    def test_simple_select(self, api):
        status, job, results = submit_and_wait(
            api, "SELECT brand_id, name FROM retail_catalog.products.brands LIMIT 5"
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert len(results["rows"]) == 5
        assert "brand_id" in results["columns"]
        assert "name" in results["columns"]

    def test_select_count(self, api):
        status, job, results = submit_and_wait(
            api, "SELECT count(*) AS cnt FROM retail_catalog.products.brands"
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert results["rows"][0]["cnt"] > 0

    def test_select_with_where(self, api):
        status, job, results = submit_and_wait(
            api, "SELECT * FROM retail_catalog.products.brands WHERE brand_id = 1"
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert len(results["rows"]) == 1

    def test_user_limit_respected(self, api):
        status, job, results = submit_and_wait(
            api, "SELECT * FROM retail_catalog.products.brands LIMIT 2"
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert len(results["rows"]) == 2


class TestComplexQueries:
    """More complex SQL patterns."""

    def test_group_by(self, api):
        status, job, results = submit_and_wait(
            api,
            "SELECT country_of_origin, count(*) AS cnt "
            "FROM retail_catalog.products.brands GROUP BY 1 LIMIT 5",
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert "country_of_origin" in results["columns"]
        assert "cnt" in results["columns"]

    def test_cte_query(self, api):
        status, job, results = submit_and_wait(
            api,
            "WITH b AS (SELECT * FROM retail_catalog.products.brands LIMIT 5) "
            "SELECT count(*) AS cnt FROM b",
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert results["rows"][0]["cnt"] == 5

    def test_same_db_join(self, api):
        status, job, results = submit_and_wait(
            api,
            "SELECT p.name, b.name AS brand "
            "FROM retail_catalog.products.products p "
            "JOIN retail_catalog.products.brands b ON p.brand_id = b.brand_id "
            "LIMIT 5",
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert "name" in results["columns"]
        assert "brand" in results["columns"]

    def test_cross_database_join(self, api):
        status, job, results = submit_and_wait(
            api,
            "SELECT c.first_name, l.tier "
            "FROM retail_sales.customers.customers c "
            "JOIN retail_sales.customers.loyalty_accounts l ON c.customer_id = l.customer_id "
            "LIMIT 5",
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert len(results["rows"]) > 0


class TestResultPagination:
    """Result response structure."""

    def test_result_pagination_fields(self, api):
        status, job, results = submit_and_wait(
            api, "SELECT * FROM retail_catalog.products.brands LIMIT 3"
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert "columns" in results
        assert "rows" in results
        assert "total" in results
        assert "page" in results
        assert "page_size" in results


class TestCreateTableViaEditor:
    """CREATE TABLE via SQL editor must land in Iceberg (visible in catalog)."""

    def test_create_table_appears_in_catalog(self, api):
        """CREATE TABLE AS SELECT via SQL editor should persist in Iceberg."""
        try:
            status, job, _ = submit_and_wait(
                api,
                f"CREATE TABLE development.{SCHEMA}.e2e_editor_tbl "
                f"AS SELECT 1 AS id, 'test' AS name",
            )
            assert status == "completed", f"Expected completed, got {job.get('error')}"

            # Table must be visible in the catalog (Iceberg), not ephemeral
            resp = api.get(
                f"/api/catalog/databases/development/schemas/{SCHEMA}/objects"
            )
            resp.raise_for_status()
            objects = resp.json()["objects"]
            names = [o["name"] for o in objects]
            assert "e2e_editor_tbl" in names, (
                f"Table not in catalog — went to in-memory overlay. Objects: {names}"
            )
        finally:
            safe_cleanup(
                api, f"DROP TABLE development.{SCHEMA}.e2e_editor_tbl"
            )
