import asyncio
import hashlib
import logging
import uuid
from datetime import datetime

import httpx
import pyarrow.ipc as ipc
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import JSONResponse
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError
from pydantic import BaseModel
from sqlalchemy import select, update

from app.config import (
    S3_ACCESS_KEY,
    S3_ENDPOINT,
    S3_REGION,
    S3_SECRET_KEY,
    WORKER_AUTH_TOKEN,
    WORKER_MODE,
    WORKER_URL,
)
from app.database import async_session
from app.models import CatalogObject, Database, QueryJob, Schema, UserApiToken, WorkerVM
from app.query_engine import _load_catalog_objects
from app.sql_rewriter import rewrite
from app.warehouse import catalog

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dbt")


async def _verify_dbt_token(authorization: str = Header()) -> int:
    """Verify per-user API token and return user_id."""
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization[len(prefix):]
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    async with async_session() as session:
        result = await session.execute(
            select(UserApiToken).where(UserApiToken.token_hash == token_hash)
        )
        user_token = result.scalar_one_or_none()
    if user_token is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user_token.user_id


async def _get_worker_url(user_id: int) -> str:
    """Resolve the worker URL for a given user.

    In local/local-worker mode, returns the static WORKER_URL.
    In remote mode, looks up the user's WorkerVM private IP.
    """
    if WORKER_MODE != "remote":
        return WORKER_URL
    async with async_session() as session:
        result = await session.execute(
            select(WorkerVM).where(
                WorkerVM.user_id == user_id,
                WorkerVM.status == "ready",
            )
        )
        vm = result.scalar_one_or_none()
    if vm is None:
        raise HTTPException(status_code=503, detail="No ready worker available")
    return f"http://{vm.private_ip}:8080"


async def _resolve_catalog_objects():
    """Load and resolve catalog objects with metadata locations."""
    catalog_objects = await _load_catalog_objects()
    resolved = []
    for obj in catalog_objects:
        entry = {
            "duckdb_schema": f"{obj['database']}.{obj['schema']}",
            "name": obj["name"],
            "object_type": obj["object_type"],
        }
        if obj["object_type"] == "table" and obj["iceberg_identifier"]:
            tbl = await asyncio.to_thread(
                catalog.load_table, obj["iceberg_identifier"]
            )
            entry["metadata_location"] = tbl.metadata_location
        elif obj["object_type"] == "view" and obj["view_sql"]:
            entry["view_sql"] = obj["view_sql"]
        resolved.append(entry)
    return resolved


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

@router.post("/session")
async def create_session(user_id: int = Depends(_verify_dbt_token)):
    """Create a worker session, provisioning a worker VM if needed.

    Returns 202 if the worker is still provisioning (adapter should poll /status).
    Returns 200 with session_id when the session is ready.
    """
    # In remote mode, ensure a worker exists for this user
    if WORKER_MODE == "remote":
        from app.worker_manager import ensure_worker
        vm = await ensure_worker(user_id)
        if vm.status == "provisioning":
            return JSONResponse(
                status_code=202,
                content={"status": "provisioning", "message": "Provisioning worker..."},
            )

    # Worker is ready — create session on it
    worker_url = await _get_worker_url(user_id)
    resolved = await _resolve_catalog_objects()
    s3_config = {
        "endpoint": S3_ENDPOINT,
        "access_key": S3_ACCESS_KEY,
        "secret_key": S3_SECRET_KEY,
        "region": S3_REGION,
    }

    headers = {"Authorization": f"Bearer {WORKER_AUTH_TOKEN}"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{worker_url}/session",
                json={"catalog_objects": resolved, "s3": s3_config},
                headers=headers,
            )
            resp.raise_for_status()
            session_id = resp.json()["session_id"]
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to create worker session: {e}")

    return {"status": "ready", "session_id": session_id}


@router.get("/session/status")
async def session_status(user_id: int = Depends(_verify_dbt_token)):
    """Poll worker provisioning status.

    Returns {"status": "ready"} when the worker is available.
    Returns {"status": "provisioning", "message": "..."} while still starting up.
    """
    if WORKER_MODE != "remote":
        return {"status": "ready"}

    async with async_session() as session:
        result = await session.execute(
            select(WorkerVM).where(
                WorkerVM.user_id == user_id,
                WorkerVM.status.in_(["provisioning", "ready"]),
            )
        )
        vm = result.scalar_one_or_none()

    if vm is None:
        raise HTTPException(status_code=404, detail="No worker found for user")

    if vm.status == "ready":
        return {"status": "ready"}

    # Still provisioning — check if the worker is actually healthy now
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"http://{vm.private_ip}:8080/health")
            if resp.status_code == 200:
                # Mark as ready
                async with async_session() as session:
                    await session.execute(
                        update(WorkerVM).where(WorkerVM.id == vm.id).values(
                            status="ready",
                        )
                    )
                    await session.commit()
                return {"status": "ready"}
    except Exception:
        pass

    return {"status": "provisioning", "message": "Worker is starting up..."}


@router.delete("/session/{session_id}")
async def delete_session(
    session_id: str,
    user_id: int = Depends(_verify_dbt_token),
):
    """Proxy session deletion to the worker."""
    worker_url = await _get_worker_url(user_id)
    headers = {"Authorization": f"Bearer {WORKER_AUTH_TOKEN}"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.delete(
                f"{worker_url}/session/{session_id}",
                headers=headers,
            )
            resp.raise_for_status()
    except Exception:
        pass  # Best-effort cleanup
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Session-config (kept for backward compatibility, but worker_auth_token removed)
# ---------------------------------------------------------------------------

@router.get("/session-config")
async def session_config(user_id: int = Depends(_verify_dbt_token)):
    """Return catalog objects with resolved metadata locations and S3 config."""
    resolved = await _resolve_catalog_objects()
    return {
        "catalog_objects": resolved,
        "s3": {
            "endpoint": S3_ENDPOINT,
            "access_key": S3_ACCESS_KEY,
            "secret_key": S3_SECRET_KEY,
            "region": S3_REGION,
        },
    }


# ---------------------------------------------------------------------------
# Query proxy
# ---------------------------------------------------------------------------

class SessionQueryProxyRequest(BaseModel):
    sql: str
    fetch_results: bool = True


@router.post("/session/{session_id}/query")
async def session_query_proxy(
    session_id: str,
    req: SessionQueryProxyRequest,
    user_id: int = Depends(_verify_dbt_token),
):
    """Proxy a dbt session query through the backend for history tracking."""
    rewritten_sql = rewrite(req.sql)

    # Create QueryJob record
    job_id = str(uuid.uuid4())
    now = datetime.utcnow()
    async with async_session() as session:
        session.add(QueryJob(
            id=job_id,
            user_id=user_id,
            sql=req.sql,
            status="running",
            started_at=now,
        ))
        await session.commit()

    # Forward to worker
    worker_url = await _get_worker_url(user_id)
    headers = {"Authorization": f"Bearer {WORKER_AUTH_TOKEN}"}
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{worker_url}/session/{session_id}/query",
                json={"sql": rewritten_sql, "fetch_results": req.fetch_results},
                headers=headers,
            )
            resp.raise_for_status()
            worker_result = resp.json()
    except Exception as e:
        async with async_session() as session:
            await session.execute(
                update(QueryJob).where(QueryJob.id == job_id).values(
                    status="failed",
                    error=str(e)[:2048],
                    completed_at=datetime.utcnow(),
                )
            )
            await session.commit()
        raise HTTPException(status_code=502, detail=str(e))

    # Record result based on worker status
    if worker_result.get("status") == "failed":
        async with async_session() as session:
            await session.execute(
                update(QueryJob).where(QueryJob.id == job_id).values(
                    status="failed",
                    error=worker_result.get("error", "Query failed")[:2048],
                    completed_at=datetime.utcnow(),
                )
            )
            await session.commit()
    else:
        row_count = worker_result.get("row_count", 0)
        async with async_session() as session:
            await session.execute(
                update(QueryJob).where(QueryJob.id == job_id).values(
                    status="completed",
                    row_count=row_count,
                    completed_at=datetime.utcnow(),
                )
            )
            await session.commit()

    return worker_result


# ---------------------------------------------------------------------------
# Materialization (server-side: backend fetches Arrow from worker + writes Iceberg)
# ---------------------------------------------------------------------------

class MaterializeRequest(BaseModel):
    database: str
    schema_name: str
    table_name: str
    duckdb_table: str


async def _write_to_iceberg(database: str, schema_name: str, table_name: str,
                            arrow_table) -> dict:
    """Write Arrow table to Iceberg and register in PostgreSQL catalog."""
    iceberg_ns = f"{database}.{schema_name}"
    iceberg_id = f"{database}.{schema_name}.{table_name}"

    def _write():
        try:
            catalog.create_namespace(iceberg_ns)
        except NamespaceAlreadyExistsError:
            pass
        try:
            tbl = catalog.load_table(iceberg_id)
            tbl.overwrite(arrow_table)
        except NoSuchTableError:
            catalog.create_table(iceberg_id, schema=arrow_table.schema)
            tbl = catalog.load_table(iceberg_id)
            tbl.overwrite(arrow_table)

    await asyncio.to_thread(_write)

    # Register in PostgreSQL catalog
    schema_id = await _ensure_db_and_schema(database, schema_name)
    async with async_session() as session:
        existing_result = await session.execute(
            select(CatalogObject).where(
                CatalogObject.schema_id == schema_id,
                CatalogObject.name == table_name,
            )
        )
        existing = existing_result.scalar_one_or_none()
        if existing is not None:
            existing.object_type = "table"
            existing.iceberg_identifier = iceberg_id
            existing.view_sql = None
        else:
            session.add(CatalogObject(
                schema_id=schema_id,
                name=table_name,
                object_type="table",
                iceberg_identifier=iceberg_id,
            ))
        await session.commit()

    logger.info("Materialized table %s (%d rows)", iceberg_id, len(arrow_table))
    return {"status": "ok", "table": iceberg_id, "rows": len(arrow_table)}


@router.post("/session/{session_id}/materialize")
async def session_materialize(
    session_id: str,
    req: MaterializeRequest,
    user_id: int = Depends(_verify_dbt_token),
):
    """Export Arrow from worker and persist to Iceberg in one step."""
    worker_url = await _get_worker_url(user_id)
    headers = {"Authorization": f"Bearer {WORKER_AUTH_TOKEN}"}

    # Fetch Arrow data from worker
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{worker_url}/session/{session_id}/export-arrow",
                json={"table": req.duckdb_table},
                headers=headers,
            )
            resp.raise_for_status()
            arrow_bytes = resp.content
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to export Arrow from worker: {e}")

    # Parse Arrow and write to Iceberg
    reader = ipc.open_stream(arrow_bytes)
    arrow_table = reader.read_all()

    return await _write_to_iceberg(req.database, req.schema_name, req.table_name, arrow_table)


# ---------------------------------------------------------------------------
# View and object management
# ---------------------------------------------------------------------------

async def _ensure_db_and_schema(db_name: str, schema_name: str) -> int:
    """Ensure Database and Schema rows exist, return schema.id."""
    async with async_session() as session:
        db_result = await session.execute(
            select(Database).where(Database.name == db_name)
        )
        db = db_result.scalar_one_or_none()
        if db is None:
            db = Database(name=db_name)
            session.add(db)
            await session.flush()

        schema_result = await session.execute(
            select(Schema).where(Schema.database_id == db.id, Schema.name == schema_name)
        )
        schema_obj = schema_result.scalar_one_or_none()
        if schema_obj is None:
            schema_obj = Schema(database_id=db.id, name=schema_name)
            session.add(schema_obj)
            await session.flush()

        schema_id = schema_obj.id
        await session.commit()
        return schema_id


class RegisterViewRequest(BaseModel):
    database: str
    schema_name: str  # 'schema' is a Pydantic reserved name
    view_name: str
    view_sql: str


@router.post("/register-view")
async def register_view(req: RegisterViewRequest, _user_id: int = Depends(_verify_dbt_token)):
    """Register a view definition in the PostgreSQL catalog."""
    schema_id = await _ensure_db_and_schema(req.database, req.schema_name)
    async with async_session() as session:
        existing_result = await session.execute(
            select(CatalogObject).where(
                CatalogObject.schema_id == schema_id,
                CatalogObject.name == req.view_name,
            )
        )
        existing = existing_result.scalar_one_or_none()
        if existing is not None:
            existing.object_type = "view"
            existing.view_sql = req.view_sql
            existing.iceberg_identifier = None
        else:
            session.add(CatalogObject(
                schema_id=schema_id,
                name=req.view_name,
                object_type="view",
                view_sql=req.view_sql,
            ))
        await session.commit()

    return {"status": "ok"}


class DropObjectRequest(BaseModel):
    database: str
    schema_name: str
    name: str
    object_type: str  # "table" or "view"


@router.post("/drop-object")
async def drop_object(req: DropObjectRequest, _user_id: int = Depends(_verify_dbt_token)):
    """Remove a catalog object. If table, also drops from Iceberg."""
    async with async_session() as session:
        db_result = await session.execute(
            select(Database).where(Database.name == req.database)
        )
        db = db_result.scalar_one_or_none()
        if db is None:
            return {"status": "ok", "detail": "database not found, nothing to drop"}

        schema_result = await session.execute(
            select(Schema).where(Schema.database_id == db.id, Schema.name == req.schema_name)
        )
        schema_obj = schema_result.scalar_one_or_none()
        if schema_obj is None:
            return {"status": "ok", "detail": "schema not found, nothing to drop"}

        existing_result = await session.execute(
            select(CatalogObject).where(
                CatalogObject.schema_id == schema_obj.id,
                CatalogObject.name == req.name,
            )
        )
        existing = existing_result.scalar_one_or_none()
        if existing is None:
            return {"status": "ok", "detail": "object not found, nothing to drop"}

        # Drop from Iceberg if it's a table
        if existing.object_type == "table" and existing.iceberg_identifier:
            iceberg_id = existing.iceberg_identifier
            try:
                await asyncio.to_thread(catalog.drop_table, iceberg_id)
            except NoSuchTableError:
                pass

        await session.delete(existing)
        await session.commit()

    return {"status": "ok"}
