"""Shared fixtures and helpers for E2E tests.

Requires: backend (port 8000), worker (port 8080), PostgreSQL, MinIO.
All E2E tests skip gracefully if services aren't running.
"""

import datetime
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


@pytest.fixture(scope="session")
def token():
    return _make_token()


@pytest.fixture(scope="session")
def api(token):
    """HTTP client with auth header."""
    with httpx.Client(
        base_url=BACKEND_URL,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    ) as client:
        yield client


@pytest.fixture(scope="session", autouse=True)
def services_available(api):
    """Skip all tests if services aren't running."""
    try:
        resp = api.get("/api/queries")
        resp.raise_for_status()
    except Exception:
        pytest.skip("Backend not available at localhost:8000")


def submit_and_wait(api, sql, timeout=15):
    """Submit a query and wait for completion.

    Returns (status, job_data, results_or_none).
    DDL returns ("ddl", data, None).
    Regular/SHOW queries poll until done.
    """
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


def dbt_query(api, session_id, sql, fetch_results=True):
    """Execute SQL in a dbt session. Returns the inline JSON response."""
    resp = api.post(
        f"/api/dbt/session/{session_id}/query",
        json={"sql": sql, "fetch_results": fetch_results},
    )
    resp.raise_for_status()
    return resp.json()


def safe_cleanup(api, sql):
    """Run a cleanup SQL statement, ignoring any errors.

    Used in fixture teardown to ensure resources are removed even if
    the test that was supposed to drop them failed mid-way.
    """
    try:
        resp = api.post("/api/queries", json={"sql": sql})
        if resp.status_code == 200:
            data = resp.json()
            # For non-DDL queries (like DROP SCHEMA which polls), wait briefly
            if "job_id" in data and "ddl_message" not in data:
                deadline = time.time() + 10
                while time.time() < deadline:
                    time.sleep(1)
                    r = api.get(f"/api/queries/{data['job_id']}")
                    if r.status_code == 200 and r.json()["status"] in ("completed", "failed"):
                        break
    except Exception:
        pass
