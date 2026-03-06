"""E2E tests for GET /api/catalog/* endpoints.

Run: cd backend && uv run pytest tests/test_catalog_api_e2e.py -v
"""

from conftest import SCHEMA, safe_cleanup, submit_and_wait


class TestListDatabases:
    """GET /api/catalog/databases."""

    def test_list_databases(self, api):
        resp = api.get("/api/catalog/databases")
        resp.raise_for_status()
        data = resp.json()
        db_names = [d["name"] for d in data]
        for expected in ("development", "retail_catalog", "retail_ops", "retail_sales"):
            assert expected in db_names, f"Expected {expected} in {db_names}"


class TestListSchemas:
    """GET /api/catalog/databases/{db}/schemas."""

    def test_list_schemas(self, api):
        resp = api.get("/api/catalog/databases/retail_catalog/schemas")
        resp.raise_for_status()
        data = resp.json()
        schema_names = [s["name"] for s in data["schemas"]]
        assert "products" in schema_names
        assert "pricing" in schema_names
        assert "total_size" in data
        assert "total_tables" in data


class TestListObjects:
    """GET /api/catalog/databases/{db}/schemas/{schema}/objects."""

    def test_list_objects(self, api):
        resp = api.get("/api/catalog/databases/retail_catalog/schemas/products/objects")
        resp.raise_for_status()
        data = resp.json()
        objects = data["objects"]
        names = [o["name"] for o in objects]
        for expected in ("brands", "categories", "suppliers", "products"):
            assert expected in names, f"Expected {expected} in {names}"
        for obj in objects:
            if obj["type"] == "table":
                assert obj["columns"] > 0

    def test_view_in_objects(self, api):
        """Create a view, verify it appears in objects list, then clean up."""
        try:
            submit_and_wait(
                api,
                f"CREATE VIEW development.{SCHEMA}.e2e_cat_obj AS SELECT 1 AS x",
            )

            resp = api.get(f"/api/catalog/databases/development/schemas/{SCHEMA}/objects")
            resp.raise_for_status()
            objects = resp.json()["objects"]
            view_names = [o["name"] for o in objects if o["type"] == "view"]
            assert "e2e_cat_obj" in view_names
        finally:
            safe_cleanup(api, f"DROP VIEW development.{SCHEMA}.e2e_cat_obj")


class TestObjectSchema:
    """GET /api/catalog/databases/{db}/schemas/{schema}/objects/{obj}/schema."""

    def test_table_schema(self, api):
        resp = api.get(
            "/api/catalog/databases/retail_catalog/schemas/products/objects/brands/schema"
        )
        resp.raise_for_status()
        data = resp.json()
        assert data["type"] == "table"
        assert len(data["columns"]) > 0
        col = data["columns"][0]
        assert "name" in col
        assert "type" in col
        assert "required" in col

    def test_view_schema(self, api):
        """Create a view, check its schema endpoint, then clean up."""
        try:
            submit_and_wait(
                api,
                f"CREATE VIEW development.{SCHEMA}.e2e_cat_schema AS SELECT 1 AS x",
            )

            resp = api.get(
                f"/api/catalog/databases/development/schemas/{SCHEMA}/objects/e2e_cat_schema/schema"
            )
            resp.raise_for_status()
            data = resp.json()
            assert data["type"] == "view"
            assert "view_sql" in data
            assert "SELECT 1 AS x" in data["view_sql"]
        finally:
            safe_cleanup(api, f"DROP VIEW development.{SCHEMA}.e2e_cat_schema")


class TestCatalogErrors:
    """404 responses for nonexistent resources."""

    def test_nonexistent_db_404(self, api):
        resp = api.get("/api/catalog/databases/nonexistent/schemas")
        assert resp.status_code == 404

    def test_nonexistent_object_404(self, api):
        resp = api.get(
            "/api/catalog/databases/retail_catalog/schemas/products/objects/nonexistent/schema"
        )
        assert resp.status_code == 404
