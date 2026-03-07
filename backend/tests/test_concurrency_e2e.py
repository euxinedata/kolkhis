"""E2E tests for concurrent operations — multiple queries and sessions in parallel.

Run: cd backend && uv run pytest tests/test_concurrency_e2e.py -v

Verifies that DuckLake handles concurrent reads, writes, and dbt sessions
correctly without data loss or corruption.
"""

import concurrent.futures
import datetime

import jwt
import httpx
import pytest

from conftest import SCHEMA, safe_cleanup, submit_and_wait

BACKEND_URL = "http://localhost:8000"
JWT_SECRET = "9b4a7672243c07b509c83ca000c5eebadeb1b8577472bdc28ec691c3535197b9"
ORG_ID = "01373afc-3ff1-4d45-9ec6-3f665c96b72e"


def _make_token(user_id="10"):
    return jwt.encode(
        {
            "sub": str(user_id),
            "email": f"user{user_id}@test.com",
            "name": f"Test User {user_id}",
            "org_id": ORG_ID,
            "org_role": "admin",
            "exp": datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
        },
        JWT_SECRET,
        algorithm="HS256",
    )


def _submit_and_wait_standalone(sql, user_id="10", timeout=20):
    """submit_and_wait using a fresh httpx client (thread-safe)."""
    import time

    token = _make_token(user_id)
    api = httpx.Client(
        base_url=BACKEND_URL,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    try:
        resp = api.post("/api/queries", json={"sql": sql})
        resp.raise_for_status()
        data = resp.json()
        if "ddl_message" in data:
            return "ddl", data, None
        job_id = data["job_id"]
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(1)
            r = api.get(f"/api/queries/{job_id}")
            r.raise_for_status()
            job = r.json()
            if job["status"] in ("completed", "failed"):
                results = None
                if job["status"] == "completed":
                    rr = api.get(f"/api/queries/{job_id}/results")
                    rr.raise_for_status()
                    results = rr.json()
                return job["status"], job, results
        return "timeout", {}, None
    finally:
        api.close()


class TestConcurrentReads:
    """Multiple SELECT queries running simultaneously."""

    def test_concurrent_selects_across_databases(self, api):
        """5 concurrent SELECTs against different databases all complete."""
        queries = [
            "SELECT count(*) AS cnt FROM retail_catalog.products.brands",
            "SELECT count(*) AS cnt FROM retail_catalog.products.products",
            "SELECT count(*) AS cnt FROM retail_ops.stores.regions",
            "SELECT count(*) AS cnt FROM retail_ops.stores.stores",
            "SELECT count(*) AS cnt FROM retail_sales.customers.customers",
        ]
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
            futures = [ex.submit(_submit_and_wait_standalone, q) for q in queries]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        for status, job, data in results:
            assert status == "completed", f"Concurrent SELECT failed: {job.get('error')}"
            assert data["rows"][0]["cnt"] > 0

    def test_concurrent_selects_same_table(self, api):
        """Multiple concurrent queries against the same table don't conflict."""
        queries = [
            "SELECT count(*) AS cnt FROM retail_catalog.products.brands",
            "SELECT name FROM retail_catalog.products.brands LIMIT 3",
            "SELECT brand_id, name FROM retail_catalog.products.brands WHERE brand_id < 5",
        ]
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
            futures = [ex.submit(_submit_and_wait_standalone, q) for q in queries]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        for status, job, _ in results:
            assert status == "completed", f"Concurrent SELECT failed: {job.get('error')}"


class TestConcurrentWrites:
    """Concurrent write operations."""

    @pytest.fixture(autouse=True)
    def _cleanup(self, api):
        yield
        for suffix in ["a", "b", "c", "shared"]:
            safe_cleanup(api, f"DROP TABLE development.{SCHEMA}.e2e_conc_{suffix}")

    def test_concurrent_creates_different_tables(self, api):
        """3 concurrent CREATE TABLE statements for different tables all succeed."""
        creates = [
            f"CREATE TABLE development.{SCHEMA}.e2e_conc_a AS SELECT 1 AS id",
            f"CREATE TABLE development.{SCHEMA}.e2e_conc_b AS SELECT 2 AS id",
            f"CREATE TABLE development.{SCHEMA}.e2e_conc_c AS SELECT 3 AS id",
        ]
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
            futures = [ex.submit(_submit_and_wait_standalone, q) for q in creates]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        for status, job, _ in results:
            assert status == "completed", f"Concurrent CREATE failed: {job.get('error')}"

        # Verify all 3 tables exist in catalog
        resp = api.get(
            f"/api/catalog/databases/development/schemas/{SCHEMA}/objects"
        )
        resp.raise_for_status()
        names = [o["name"] for o in resp.json()["objects"]]
        for suffix in ["a", "b", "c"]:
            assert f"e2e_conc_{suffix}" in names, f"Table e2e_conc_{suffix} missing: {names}"

    def test_concurrent_inserts_same_table(self, api):
        """3 concurrent INSERTs into the same table — all rows land."""
        submit_and_wait(
            api,
            f"CREATE TABLE development.{SCHEMA}.e2e_conc_shared AS SELECT 0 AS id",
        )

        inserts = [
            f"INSERT INTO development.{SCHEMA}.e2e_conc_shared VALUES ({i})"
            for i in range(1, 4)
        ]
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
            futures = [ex.submit(_submit_and_wait_standalone, q) for q in inserts]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        for status, job, _ in results:
            assert status == "completed", f"Concurrent INSERT failed: {job.get('error')}"

        # All 4 rows should be present (initial + 3 inserts)
        status, job, data = submit_and_wait(
            api,
            f"SELECT * FROM development.{SCHEMA}.e2e_conc_shared ORDER BY id",
        )
        assert status == "completed"
        ids = [r["id"] for r in data["rows"]]
        assert ids == [0, 1, 2, 3], f"Expected [0,1,2,3], got {ids}"

    def test_concurrent_read_during_write(self, api):
        """SELECT while INSERT is running — both complete without error."""
        submit_and_wait(
            api,
            f"CREATE TABLE development.{SCHEMA}.e2e_conc_shared "
            f"AS SELECT 1 AS id, 'init' AS val",
        )

        def do_insert():
            return _submit_and_wait_standalone(
                f"INSERT INTO development.{SCHEMA}.e2e_conc_shared VALUES (2, 'added')"
            )

        def do_select():
            return _submit_and_wait_standalone(
                f"SELECT count(*) AS cnt FROM development.{SCHEMA}.e2e_conc_shared"
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            f_insert = ex.submit(do_insert)
            f_select = ex.submit(do_select)
            insert_result = f_insert.result()
            select_result = f_select.result()

        assert insert_result[0] == "completed", f"INSERT failed: {insert_result[1].get('error')}"
        assert select_result[0] == "completed", f"SELECT failed: {select_result[1].get('error')}"


class TestConcurrentDbtSessions:
    """Multiple dbt sessions running simultaneously (different users)."""

    def test_concurrent_sessions_different_users(self, api):
        """3 users each create a dbt session and query simultaneously."""
        def run_session(user_id):
            token = _make_token(user_id)
            client = httpx.Client(
                base_url=BACKEND_URL,
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            try:
                resp = client.post("/api/dbt/session")
                resp.raise_for_status()
                sid = resp.json()["session_id"]

                r = client.post(
                    f"/api/dbt/session/{sid}/query",
                    json={"sql": f"SELECT {user_id} AS uid", "fetch_results": True},
                )
                r.raise_for_status()
                result = r.json()

                client.delete(f"/api/dbt/session/{sid}")
                return user_id, result["status"], result["rows"][0][0]
            finally:
                client.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
            futures = [ex.submit(run_session, uid) for uid in [10, 11, 12]]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        for uid, status, val in results:
            assert status == "completed", f"User {uid} session query failed"
            assert val == uid, f"User {uid} got wrong value: {val}"
