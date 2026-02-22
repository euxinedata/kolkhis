import asyncio
import os
from datetime import datetime

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import select, update

from app.config import MAX_RESULT_ROWS, RESULTS_PATH, WAREHOUSE_PATH
from app.database import async_session
from app.models import CatalogObject, Database, QueryJob, Schema
from app.warehouse import catalog

_running_tasks: dict[str, asyncio.Task] = {}


def _result_path(job_id: str) -> str:
    return os.path.join(RESULTS_PATH, f"{job_id}.parquet")


async def _load_catalog_objects() -> list[dict]:
    """Load all catalog objects with their database and schema names."""
    async with async_session() as session:
        result = await session.execute(
            select(CatalogObject, Schema, Database)
            .join(Schema, CatalogObject.schema_id == Schema.id)
            .join(Database, Schema.database_id == Database.id)
        )
        objects = []
        for obj, schema, database in result.all():
            objects.append(
                {
                    "name": obj.name,
                    "object_type": obj.object_type,
                    "iceberg_identifier": obj.iceberg_identifier,
                    "view_sql": obj.view_sql,
                    "database": database.name,
                    "schema": schema.name,
                }
            )
        return objects


def _rewrite_three_part_names(sql: str, catalog_objects: list[dict]) -> str:
    """Rewrite database.schema.table references to database__schema.table for DuckDB.

    DuckDB only supports two-level identifiers (schema.table). We rewrite any
    three-part name that matches a known catalog object so users can write
    natural SQL like: SELECT * FROM kolkhis.nyc.yellow_trips
    """
    import re

    # Build a map of (db, schema, name) -> duckdb reference
    replacements: list[tuple[str, str]] = []
    for obj in catalog_objects:
        # Match db.schema.name (with optional quoting)
        db, schema, name = obj["database"], obj["schema"], obj["name"]
        # Three-part: db.schema.name -> db__schema.name
        three_part = f"{db}.{schema}.{name}"
        duckdb_ref = f'"{db}__{schema}"."{name}"'
        replacements.append((three_part, duckdb_ref))

    # Sort longest first to avoid partial matches
    replacements.sort(key=lambda x: len(x[0]), reverse=True)

    for pattern, replacement in replacements:
        # Replace as whole identifier (case-insensitive)
        sql = re.sub(re.escape(pattern), replacement, sql, flags=re.IGNORECASE)

    return sql


def _run_duckdb(sql: str, job_id: str, catalog_objects: list[dict]) -> int:
    """Run a SQL query via DuckDB against registered catalog objects. Returns row count."""
    conn = duckdb.connect()
    try:
        conn.install_extension("iceberg")
        conn.load_extension("iceberg")

        # Register objects from metadata layer
        created_schemas: set[str] = set()
        for obj in catalog_objects:
            duckdb_schema = f"{obj['database']}__{obj['schema']}"

            if duckdb_schema not in created_schemas:
                conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{duckdb_schema}"')
                created_schemas.add(duckdb_schema)

            if obj["object_type"] == "table" and obj["iceberg_identifier"]:
                tbl = catalog.load_table(obj["iceberg_identifier"])
                metadata_path = tbl.metadata_location
                conn.execute(
                    f'CREATE VIEW "{duckdb_schema}"."{obj["name"]}" AS '
                    f"SELECT * FROM iceberg_scan('{metadata_path}')"
                )
            elif obj["object_type"] == "view" and obj["view_sql"]:
                conn.execute(
                    f'CREATE VIEW "{duckdb_schema}"."{obj["name"]}" AS '
                    f'{obj["view_sql"]}'
                )

        # Rewrite three-part names to DuckDB two-part names
        sql = _rewrite_three_part_names(sql, catalog_objects)

        # Execute with row limit
        limited_sql = f"SELECT * FROM ({sql}) AS _q LIMIT {MAX_RESULT_ROWS}"
        result = conn.execute(limited_sql)

        # Fetch as PyArrow and write to Parquet
        arrow_table = result.fetch_arrow_table()
        row_count = arrow_table.num_rows
        pq.write_table(arrow_table, _result_path(job_id))
        return row_count
    finally:
        conn.close()


async def _update_job(job_id: str, **kwargs):
    async with async_session() as session:
        await session.execute(
            update(QueryJob).where(QueryJob.id == job_id).values(**kwargs)
        )
        await session.commit()


async def execute_query(job_id: str, sql: str):
    await _update_job(job_id, status="running", started_at=datetime.utcnow())
    try:
        # Load catalog objects from metadata layer (async)
        catalog_objects = await _load_catalog_objects()
        # Run DuckDB in a thread (sync)
        row_count = await asyncio.to_thread(_run_duckdb, sql, job_id, catalog_objects)
        await _update_job(
            job_id,
            status="completed",
            row_count=row_count,
            completed_at=datetime.utcnow(),
        )
    except Exception as e:
        await _update_job(
            job_id,
            status="failed",
            error=str(e)[:2048],
            completed_at=datetime.utcnow(),
        )


def submit_query(job_id: str, sql: str):
    task = asyncio.create_task(execute_query(job_id, sql))
    _running_tasks[job_id] = task

    def _on_done(_t):
        _running_tasks.pop(job_id, None)

    task.add_done_callback(_on_done)
