import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.config import (
    LAKEKEEPER_URL,
    S3_ACCESS_KEY,
    S3_ENDPOINT,
    S3_REGION,
    S3_SECRET_KEY,
    WORKER_AUTH_TOKEN,
    WORKER_URL,
)
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dbt")

# Track active dbt sessions: user_id -> {session_id, worker_url}
_active_sessions: dict[int, dict] = {}


def _worker_headers() -> dict:
    return {"Authorization": f"Bearer {WORKER_AUTH_TOKEN}"}


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
                    f"{WORKER_URL}/session/{existing['session_id']}/keepalive",
                    headers=_worker_headers(),
                )
                if resp.status_code == 200:
                    return {"session_id": existing["session_id"]}
        except Exception:
            pass
        _active_sessions.pop(user_id, None)

    # Create new Iceberg session on the worker
    payload = {
        "lakekeeper_url": LAKEKEEPER_URL,
        "warehouse": "warehouse",
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
                f"{WORKER_URL}/session/iceberg",
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
    _active_sessions[user_id] = {"session_id": session_id}
    return {"session_id": session_id}


@router.post("/session/{session_id}/query")
async def session_query(
    session_id: str,
    body: SessionQueryRequest,
    user: dict = Depends(require_auth),
):
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{WORKER_URL}/session/{session_id}/query",
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
    _active_sessions.pop(user_id, None)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.delete(
                f"{WORKER_URL}/session/{session_id}",
                headers=_worker_headers(),
            )
            resp.raise_for_status()
            return {"status": "closed"}
    except Exception:
        return {"status": "closed"}
