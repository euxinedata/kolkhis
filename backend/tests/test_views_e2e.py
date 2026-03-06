"""End-to-end view tests against running services.

Requires: backend (port 8000), worker (port 8080), PostgreSQL, Lakekeeper, MinIO.
Tests the actual user flows: CREATE VIEW → query → DROP VIEW through the real
API endpoints, hitting real PostgreSQL storage and real Iceberg databases.

Run: cd backend && uv run pytest tests/test_views_e2e.py -v
"""

import time

import jwt
import httpx
import pytest

BACKEND_URL = "http://localhost:8000"
JWT_SECRET = "9b4a7672243c07b509c83ca000c5eebadeb1b8577472bdc28ec691c3535197b9"
ORG_ID = "01373afc-3ff1-4d45-9ec6-3f665c96b72e"
USER_ID = "10"
SCHEMA = "dbt_petkov_venelin"


def _make_token():
    import datetime
    return jwt.encode(
        {
            "sub": USER_ID,
            "email": "test@test.com",
            "name": "Test User",
            "org_id": ORG_ID,
            "org_role": "admin",
            "exp": datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
        },
        JWT_SECRET,
        algorithm="HS256",
    )


@pytest.fixture(scope="module")
def token():
    return _make_token()


@pytest.fixture(scope="module")
def api(token):
    """HTTP client with auth header."""
    return httpx.Client(
        base_url=BACKEND_URL,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )


def _submit_and_wait(api, sql, timeout=15):
    """Submit a query and wait for completion. Returns (status, job_data, results_or_none)."""
    resp = api.post("/api/queries", json={"sql": sql})
    resp.raise_for_status()
    data = resp.json()

    # DDL returns immediately with ddl_message
    if "ddl_message" in data:
        return "ddl", data, None

    job_id = data["job_id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1)
        resp = api.get(f"/api/queries/{job_id}")
        resp.raise_for_status()
        job = resp.json()
        if job["status"] in ("completed", "failed", "cancelled"):
            results = None
            if job["status"] == "completed":
                resp = api.get(f"/api/queries/{job_id}/results")
                resp.raise_for_status()
                results = resp.json()
            return job["status"], job, results

    pytest.fail(f"Query did not complete within {timeout}s: {sql}")


@pytest.fixture(scope="module")
def services_available(api):
    """Skip all tests if services aren't running."""
    try:
        resp = api.get("/api/queries")
        resp.raise_for_status()
    except Exception:
        pytest.skip("Backend not available at localhost:8000")


class TestSqlEditorViewLifecycle:
    """User creates a view, queries it, updates it, drops it — all through the SQL editor API."""

    def test_create_simple_view(self, api, services_available):
        status, data, _ = _submit_and_wait(
            api, f"CREATE VIEW development.{SCHEMA}.e2e_test_view AS SELECT 42 AS answer, 'hello' AS greeting"
        )
        assert status == "ddl"
        assert "created" in data["ddl_message"]

    def test_query_simple_view(self, api, services_available):
        status, job, results = _submit_and_wait(
            api, f"SELECT * FROM development.{SCHEMA}.e2e_test_view"
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert results["columns"] == ["answer", "greeting"]
        assert results["rows"] == [{"answer": 42, "greeting": "hello"}]

    def test_create_or_replace_view(self, api, services_available):
        status, data, _ = _submit_and_wait(
            api, f"CREATE OR REPLACE VIEW development.{SCHEMA}.e2e_test_view AS SELECT 99 AS answer"
        )
        assert status == "ddl"

    def test_query_replaced_view(self, api, services_available):
        status, job, results = _submit_and_wait(
            api, f"SELECT * FROM development.{SCHEMA}.e2e_test_view"
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert results["columns"] == ["answer"]
        assert results["rows"] == [{"answer": 99}]

    def test_drop_view(self, api, services_available):
        status, data, _ = _submit_and_wait(
            api, f"DROP VIEW development.{SCHEMA}.e2e_test_view"
        )
        assert status == "ddl"
        assert "dropped" in data["ddl_message"]

    def test_query_dropped_view_fails(self, api, services_available):
        status, job, _ = _submit_and_wait(
            api, f"SELECT * FROM development.{SCHEMA}.e2e_test_view"
        )
        assert status == "failed"
        assert "e2e_test_view" in job["error"]


class TestCrossDatabaseView:
    """View in development referencing a table in another Iceberg database."""

    def test_create_cross_db_view(self, api, services_available):
        status, data, _ = _submit_and_wait(
            api,
            f"CREATE VIEW development.{SCHEMA}.e2e_cross_db AS "
            "SELECT customer_id, tier FROM retail_sales.customers.loyalty_accounts LIMIT 5",
        )
        assert status == "ddl"

    def test_query_cross_db_view(self, api, services_available):
        status, job, results = _submit_and_wait(
            api, f"SELECT * FROM development.{SCHEMA}.e2e_cross_db"
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert "customer_id" in results["columns"]
        assert "tier" in results["columns"]
        assert results["total"] == 5

    def test_cleanup_cross_db_view(self, api, services_available):
        status, _, _ = _submit_and_wait(
            api, f"DROP VIEW development.{SCHEMA}.e2e_cross_db"
        )
        assert status == "ddl"


class TestViewWithRealTableData:
    """View referencing an actual Iceberg table with real data."""

    def test_create_aggregation_view(self, api, services_available):
        status, data, _ = _submit_and_wait(
            api,
            f"CREATE VIEW development.{SCHEMA}.e2e_brand_count AS "
            "SELECT count(*) AS cnt FROM retail_catalog.products.brands",
        )
        assert status == "ddl"

    def test_query_aggregation_view(self, api, services_available):
        status, job, results = _submit_and_wait(
            api, f"SELECT * FROM development.{SCHEMA}.e2e_brand_count"
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert results["columns"] == ["cnt"]
        assert results["rows"][0]["cnt"] > 0

    def test_cleanup_aggregation_view(self, api, services_available):
        status, _, _ = _submit_and_wait(
            api, f"DROP VIEW development.{SCHEMA}.e2e_brand_count"
        )
        assert status == "ddl"


class TestViewInCatalogSidebar:
    """Views should appear in the catalog API."""

    def test_view_appears_in_objects_list(self, api, services_available):
        # Create a view
        _submit_and_wait(
            api,
            f"CREATE VIEW development.{SCHEMA}.e2e_catalog_test AS SELECT 1 AS x",
        )

        # Check catalog API
        resp = api.get(f"/api/catalog/databases/development/schemas/{SCHEMA}/objects")
        resp.raise_for_status()
        objects = resp.json()["objects"]
        view_names = [o["name"] for o in objects if o["type"] == "view"]
        assert "e2e_catalog_test" in view_names

        # Cleanup
        _submit_and_wait(
            api, f"DROP VIEW development.{SCHEMA}.e2e_catalog_test"
        )

    def test_view_schema_endpoint(self, api, services_available):
        # Create a view
        _submit_and_wait(
            api,
            f"CREATE VIEW development.{SCHEMA}.e2e_schema_test AS SELECT 1 AS x",
        )

        # Check object schema API
        resp = api.get(
            f"/api/catalog/databases/development/schemas/{SCHEMA}/objects/e2e_schema_test/schema"
        )
        resp.raise_for_status()
        data = resp.json()
        assert data["type"] == "view"
        assert "SELECT 1 AS x" in data["view_sql"]

        # Cleanup
        _submit_and_wait(
            api, f"DROP VIEW development.{SCHEMA}.e2e_schema_test"
        )
