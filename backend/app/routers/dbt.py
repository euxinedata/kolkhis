import asyncio
import logging

import pyarrow.ipc as ipc
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError
from pydantic import BaseModel
from sqlalchemy import select

from app.config import (
    S3_ACCESS_KEY,
    S3_ENDPOINT,
    S3_REGION,
    S3_SECRET_KEY,
    WORKER_AUTH_TOKEN,
)
from app.database import async_session
from app.models import CatalogObject, Database, Schema
from app.query_engine import _load_catalog_objects
from app.warehouse import catalog

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dbt")


def _verify_token(authorization: str = Header()) -> None:
    """Verify bearer token matches the shared worker auth token."""
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    if authorization[len(prefix):] != WORKER_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")


@router.get("/session-config")
async def session_config(_: None = Depends(_verify_token)):
    """Return catalog objects with resolved metadata locations and S3 config.

    Used by the dbt-kolkhis adapter to create a worker session.
    """
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

    return {
        "catalog_objects": resolved,
        "s3": {
            "endpoint": S3_ENDPOINT,
            "access_key": S3_ACCESS_KEY,
            "secret_key": S3_SECRET_KEY,
            "region": S3_REGION,
        },
    }


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


@router.post("/materialize")
async def materialize(
    _: None = Depends(_verify_token),
    arrow_data: UploadFile = File(...),
    database: str = Form(...),
    schema: str = Form(...),
    table_name: str = Form(...),
):
    """Persist an Arrow table to Iceberg and register in the catalog."""
    raw = await arrow_data.read()
    reader = ipc.open_stream(raw)
    arrow_table = reader.read_all()

    iceberg_ns = f"{database}.{schema}"
    iceberg_id = f"{database}.{schema}.{table_name}"

    def _write_iceberg():
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

    await asyncio.to_thread(_write_iceberg)

    # Register in PostgreSQL catalog
    schema_id = await _ensure_db_and_schema(database, schema)
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


class RegisterViewRequest(BaseModel):
    database: str
    schema_name: str  # 'schema' is a Pydantic reserved name
    view_name: str
    view_sql: str


@router.post("/register-view")
async def register_view(req: RegisterViewRequest, _: None = Depends(_verify_token)):
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
async def drop_object(req: DropObjectRequest, _: None = Depends(_verify_token)):
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
