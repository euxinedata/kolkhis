import logging
import re
import uuid
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.config import (
    DUCKLAKE_PG_CONNECTION,
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
from app.models import OrgDatabase, QueryJob

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

    # Create new DuckLake session on the worker
    org_id = user.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No active organization")

    # Look up org databases
    result = await db.execute(
        select(OrgDatabase).where(OrgDatabase.org_id == org_id)
    )
    org_databases = result.scalars().all()
    databases = [
        {"name": d.name, "data_path": d.data_path, "metadata_schema": d.metadata_schema}
        for d in org_databases
    ]

    worker_url = await _get_worker_url(user_id)

    payload = {
        "pg_connection_string": DUCKLAKE_PG_CONNECTION,
        "databases": databases,
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
                f"{worker_url}/session/ducklake",
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


_DBT_COMMENT_RE = re.compile(r"^\s*/\*.*?\*/\s*", re.DOTALL)


def _strip_dbt_comment(sql: str) -> str:
    """Strip the leading /* ... */ comment block that dbt prepends to every query."""
    return _DBT_COMMENT_RE.sub("", sql)


async def _touch_worker_vm(user_id: int):
    """Update last_query_at on the user's WorkerVM so the idle reaper won't kill it."""
    if WORKER_MODE != "remote":
        return
    try:
        from app.database import async_session
        from app.models import WorkerVM
        async with async_session() as session:
            await session.execute(
                update(WorkerVM).where(WorkerVM.user_id == user_id).values(
                    last_query_at=datetime.utcnow()
                )
            )
            await session.commit()
    except Exception:
        logger.debug("Failed to update WorkerVM.last_query_at for user %d", user_id)


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

    # Record query in history
    job_id = str(uuid.uuid4())
    now = datetime.utcnow()
    job = QueryJob(
        id=job_id, user_id=user_id, sql=body.sql,
        status="running", started_at=now,
    )
    db.add(job)
    await db.commit()

    # Update worker VM timeout
    await _touch_worker_vm(user_id)

    try:
        # Strip dbt's leading /* ... */ comment for pattern matching
        clean_sql = _strip_dbt_comment(body.sql)

        # Intercept DDL that affects OrgDatabase records (CREATE/DROP DATABASE)
        ddl = detect_ddl(clean_sql)
        if ddl and ddl["op"] in ("create_database", "drop_database", "rename_database"):
            await execute_ddl(ddl, org_id, db)
            result = {"status": "completed", "columns": None, "rows": None, "row_count": 0}
            await _record_result(db, job_id, result)
            return result

        # All other SQL — forward as-is to worker (DuckLake handles everything natively)
        result = await _forward_query(worker_url, session_id, body.sql, body.fetch_results)
        await _record_result(db, job_id, result)
        return result

    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            await _record_result(db, job_id, {"status": "failed", "error": "Session not found"})
            raise HTTPException(status_code=404, detail="Session not found")
        error_msg = f"Worker error: {exc.response.text}"
        await _record_result(db, job_id, {"status": "failed", "error": error_msg})
        raise HTTPException(status_code=502, detail=error_msg)
    except httpx.TransportError as exc:
        error_msg = f"Worker unreachable: {exc}"
        await _record_result(db, job_id, {"status": "failed", "error": error_msg})
        raise HTTPException(status_code=502, detail=error_msg)
    except ValueError as exc:
        result = {"status": "failed", "error": str(exc)}
        await _record_result(db, job_id, result)
        return result


async def _record_result(db: AsyncSession, job_id: str, result: dict):
    """Update the QueryJob with the worker result."""
    now = datetime.utcnow()
    status = result.get("status", "completed")
    error = result.get("error") if status == "failed" else None
    row_count = result.get("row_count")
    await db.execute(
        update(QueryJob).where(QueryJob.id == job_id).values(
            status=status,
            error=error[:2048] if error else None,
            row_count=row_count,
            completed_at=now,
        )
    )
    await db.commit()


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
