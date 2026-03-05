import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

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

    worker_url = await _get_worker_url(user_id)

    payload = {
        "lakekeeper_url": LAKEKEEPER_WORKER_URL,
        "warehouse": org_id,
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


@router.post("/session/{session_id}/query")
async def session_query(
    session_id: str,
    body: SessionQueryRequest,
    user: dict = Depends(require_auth),
):
    user_id = int(user["sub"])
    existing = _active_sessions.get(user_id)
    if not existing or existing["session_id"] != session_id:
        raise HTTPException(status_code=404, detail="Session not found")
    worker_url = existing["worker_url"]

    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{worker_url}/session/{session_id}/query",
                json={"sql": body.sql, "fetch_results": body.fetch_results},
                headers=_worker_headers(),
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Session not found")
        raise HTTPException(status_code=502, detail=f"Worker error: {exc.response.text}")
    except httpx.TransportError as exc:
        raise HTTPException(status_code=502, detail=f"Worker unreachable: {exc}")


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
