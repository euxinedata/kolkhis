"""E2E tests for SHOW DATABASES/SCHEMAS/TABLES commands.

Read-only tests against existing retail data.
Run: cd backend && uv run pytest tests/test_show_commands_e2e.py -v
"""

import pytest

from conftest import SCHEMA, safe_cleanup, submit_and_wait


class TestShowDatabases:
    """SHOW DATABASES returns known databases."""

    def test_show_databases_returns_known(self, api):
        status, job, results = submit_and_wait(api, "SHOW DATABASES")
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        db_names = [r["database_name"] for r in results["rows"]]
        for expected in ("development", "retail_catalog", "retail_ops", "retail_sales"):
            assert expected in db_names, f"Expected {expected} in {db_names}"

    def test_show_databases_column_name(self, api):
        status, _, results = submit_and_wait(api, "SHOW DATABASES")
        assert status == "completed"
        assert results["columns"] == ["database_name"]


class TestShowSchemas:
    """SHOW SCHEMAS IN <database> returns known schemas."""

    def test_show_schemas_retail_catalog(self, api):
        status, job, results = submit_and_wait(api, "SHOW SCHEMAS IN retail_catalog")
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        schema_names = [r["schema_name"] for r in results["rows"]]
        assert "products" in schema_names
        assert "pricing" in schema_names

    def test_show_schemas_retail_sales(self, api):
        status, job, results = submit_and_wait(api, "SHOW SCHEMAS IN retail_sales")
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        schema_names = [r["schema_name"] for r in results["rows"]]
        assert "customers" in schema_names
        assert "transactions" in schema_names

    def test_show_schemas_column_name(self, api):
        status, _, results = submit_and_wait(api, "SHOW SCHEMAS IN development")
        assert status == "completed"
        assert results["columns"] == ["schema_name"]

    def test_show_schemas_nonexistent_db(self, api):
        resp = api.post("/api/queries", json={"sql": "SHOW SCHEMAS IN nonexistent"})
        assert resp.status_code == 400
        assert "not found" in resp.json()["detail"].lower()


class TestShowTables:
    """SHOW TABLES IN <database>.<schema> returns known tables."""

    def test_show_tables_products(self, api):
        status, job, results = submit_and_wait(
            api, "SHOW TABLES IN retail_catalog.products"
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        names = [r["table_name"] for r in results["rows"]]
        for expected in ("brands", "categories", "suppliers", "products"):
            assert expected in names, f"Expected {expected} in {names}"
        # All should be TABLE type
        types = {r["table_name"]: r["table_type"] for r in results["rows"]}
        for name in ("brands", "categories", "suppliers", "products"):
            assert types[name] == "TABLE"

    def test_show_tables_columns(self, api):
        status, _, results = submit_and_wait(
            api, "SHOW TABLES IN retail_catalog.products"
        )
        assert status == "completed"
        assert results["columns"] == ["table_name", "table_type"]

    def test_show_tables_with_view(self, api):
        """Create a view, verify it appears in SHOW TABLES, then clean up."""
        try:
            submit_and_wait(
                api,
                f"CREATE VIEW development.{SCHEMA}.e2e_show_test AS SELECT 1 AS x",
            )

            status, job, results = submit_and_wait(
                api, f"SHOW TABLES IN development.{SCHEMA}"
            )
            assert status == "completed", f"Expected completed, got {job.get('error')}"
            entries = {r["table_name"]: r["table_type"] for r in results["rows"]}
            assert "e2e_show_test" in entries
            assert entries["e2e_show_test"] == "VIEW"
        finally:
            safe_cleanup(api, f"DROP VIEW development.{SCHEMA}.e2e_show_test")
