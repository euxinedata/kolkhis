import logging
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.config import (
    LAKEKEEPER_WORKER_URL,
    S3_ACCESS_KEY,
    S3_ENDPOINT,
    S3_REGION,
    S3_SECRET_KEY,
    WORKER_AUTH_TOKEN,
    WORKER_MODE,
    WORKER_URL,
)
from app.database import get_db
from app.ddl import detect_ddl, execute_ddl
from app.models import OrgDatabase, OrgView

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dbt")

# Track active dbt sessions: user_id -> {session_id, worker_url}
_active_sessions: dict[int, dict] = {}


def _worker_headers() -> dict:
    return {"Authorization": f"Bearer {WORKER_AUTH_TOKEN}"}


async def _get_worker_url(user_id: int) -> str:
    """Resolve the worker URL based on WORKER_MODE."""
    if WORKER_MODE == "remote":
        from sqlalchemy import select

        from app.database import async_session
        from app.models import WorkerVM
        from app.worker_manager import ensure_worker, wait_for_ready

        vm = await ensure_worker(user_id)
        if vm.status == "provisioning":
            await wait_for_ready(vm.id)
            async with async_session() as session:
                result = await session.execute(
                    select(WorkerVM).where(WorkerVM.id == vm.id)
                )
                vm = result.scalar_one()
        return f"http://{vm.private_ip}:8080"
    return WORKER_URL


class SessionQueryRequest(BaseModel):
    sql: str
    fetch_results: bool = True


@router.post("/session")
async def create_session(
    user: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    user_id = int(user["sub"])

    # Reuse existing session if still alive
    existing = _active_sessions.get(user_id)
    if existing:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(
                    f"{existing['worker_url']}/session/{existing['session_id']}/keepalive",
                    headers=_worker_headers(),
                )
                if resp.status_code == 200:
                    return {"session_id": existing["session_id"]}
        except Exception:
            pass
        _active_sessions.pop(user_id, None)

    # Create new Iceberg session on the worker
    org_id = user.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No active organization")

    # Look up org databases and views
    result = await db.execute(
        select(OrgDatabase).where(OrgDatabase.org_id == org_id)
    )
    org_databases = result.scalars().all()
    databases = [
        {"name": d.name, "lakekeeper_warehouse": d.lakekeeper_warehouse}
        for d in org_databases
    ]
    result = await db.execute(
        select(OrgView).where(OrgView.org_id == org_id)
    )
    org_views = result.scalars().all()
    views = [
        {"database": v.database, "schema_name": v.schema_name, "name": v.name, "view_sql": v.view_sql}
        for v in org_views
    ]

    worker_url = await _get_worker_url(user_id)

    payload = {
        "lakekeeper_url": LAKEKEEPER_WORKER_URL,
        "databases": databases,
        "views": views,
        "s3": {
            "endpoint": S3_ENDPOINT,
            "access_key": S3_ACCESS_KEY,
            "secret_key": S3_SECRET_KEY,
            "region": S3_REGION,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{worker_url}/session/iceberg",
                json=payload,
                headers=_worker_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Worker error: {exc.response.text}")
    except httpx.TransportError as exc:
        raise HTTPException(status_code=502, detail=f"Worker unreachable: {exc}")

    session_id = data["session_id"]
    _active_sessions[user_id] = {"session_id": session_id, "worker_url": worker_url}
    return {"session_id": session_id}


async def _forward_query(worker_url: str, session_id: str, sql: str, fetch_results: bool = True) -> dict:
    """Forward a SQL query to the worker session."""
    async with httpx.AsyncClient(timeout=3600) as client:
        resp = await client.post(
            f"{worker_url}/session/{session_id}/query",
            json={"sql": sql, "fetch_results": fetch_results},
            headers=_worker_headers(),
        )
        resp.raise_for_status()
        return resp.json()


# Regex for rewriting CREATE TABLE/DROP TABLE to target _ice_ prefix in overlay mode
_CREATE_TABLE_RE = re.compile(
    r"^(\s*CREATE\s+(?:OR\s+REPLACE\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?)"
    r'"?(\w+)"?\."?(\w+)"?\."?(\w+)"?'
    r"(\s+.*)",
    re.IGNORECASE | re.DOTALL,
)
_DROP_TABLE_RE = re.compile(
    r"^(\s*DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?)"
    r'"?(\w+)"?\."?(\w+)"?\."?(\w+)"?'
    r"(\s*;?\s*)$",
    re.IGNORECASE,
)
_CREATE_SCHEMA_RE = re.compile(
    r"^(\s*CREATE\s+SCHEMA\s+(?:IF\s+NOT\s+EXISTS\s+)?)"
    r'"?(\w+)"?\."?(\w+)"?'
    r"(\s*;?\s*)$",
    re.IGNORECASE,
)


@router.post("/session/{session_id}/query")
async def session_query(
    session_id: str,
    body: SessionQueryRequest,
    user: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    user_id = int(user["sub"])
    existing = _active_sessions.get(user_id)
    if not existing or existing["session_id"] != session_id:
        raise HTTPException(status_code=404, detail="Session not found")
    worker_url = existing["worker_url"]
    org_id = user.get("org_id", "")

    try:
        # Intercept CREATE/DROP VIEW — store in PostgreSQL, forward to worker session
        ddl = detect_ddl(body.sql)
        if ddl and ddl["op"] == "create_view":
            await execute_ddl(ddl, org_id, db)
            return await _forward_query(worker_url, session_id, body.sql, body.fetch_results)

        if ddl and ddl["op"] == "drop_view":
            await execute_ddl(ddl, org_id, db)
            return await _forward_query(worker_url, session_id, body.sql, body.fetch_results)

        # Intercept CREATE TABLE — rewrite to target _ice_ prefix, then create pass-through view
        m = _CREATE_TABLE_RE.match(body.sql)
        if m:
            prefix, db_name, schema_name, table_name, rest = m.groups()
            ice_sql = f'{prefix}"_ice_{db_name}"."{schema_name}"."{table_name}"{rest}'
            result = await _forward_query(worker_url, session_id, ice_sql, body.fetch_results)
            # Create pass-through view in the memory overlay
            view_sql = (
                f'CREATE OR REPLACE VIEW "{db_name}"."{schema_name}"."{table_name}" '
                f'AS SELECT * FROM "_ice_{db_name}"."{schema_name}"."{table_name}"'
            )
            await _forward_query(worker_url, session_id, view_sql, fetch_results=False)
            return result

        # Intercept DROP TABLE — rewrite to _ice_ prefix, drop pass-through view
        m = _DROP_TABLE_RE.match(body.sql)
        if m:
            prefix, db_name, schema_name, table_name, _ = m.groups()
            ice_sql = f'{prefix}"_ice_{db_name}"."{schema_name}"."{table_name}"'
            result = await _forward_query(worker_url, session_id, ice_sql, body.fetch_results)
            drop_view_sql = f'DROP VIEW IF EXISTS "{db_name}"."{schema_name}"."{table_name}"'
            await _forward_query(worker_url, session_id, drop_view_sql, fetch_results=False)
            return result

        # Intercept CREATE SCHEMA — forward to both _ice_ and overlay databases
        m = _CREATE_SCHEMA_RE.match(body.sql)
        if m:
            prefix, db_name, schema_name, _ = m.groups()
            ice_sql = f'{prefix}"_ice_{db_name}"."{schema_name}"'
            await _forward_query(worker_url, session_id, ice_sql, fetch_results=False)
            overlay_sql = f'{prefix}"{db_name}"."{schema_name}"'
            return await _forward_query(worker_url, session_id, overlay_sql, body.fetch_results)

        # All other SQL — forward as-is
        return await _forward_query(worker_url, session_id, body.sql, body.fetch_results)

    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Session not found")
        raise HTTPException(status_code=502, detail=f"Worker error: {exc.response.text}")
    except httpx.TransportError as exc:
        raise HTTPException(status_code=502, detail=f"Worker unreachable: {exc}")
    except ValueError as exc:
        return {"status": "failed", "error": str(exc)}


@router.delete("/session/{session_id}")
async def close_session(
    session_id: str,
    user: dict = Depends(require_auth),
):
    user_id = int(user["sub"])
    existing = _active_sessions.pop(user_id, None)
    worker_url = existing["worker_url"] if existing else WORKER_URL

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.delete(
                f"{worker_url}/session/{session_id}",
                headers=_worker_headers(),
            )
            resp.raise_for_status()
            return {"status": "closed"}
    except Exception:
        return {"status": "closed"}
