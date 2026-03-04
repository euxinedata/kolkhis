import asyncio
import os
import re
import shutil
from datetime import datetime

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import delete, select, update

from app.config import (
    MAX_RESULT_ROWS,
    RESULTS_PATH,
    S3_ACCESS_KEY,
    S3_ENDPOINT,
    S3_REGION,
    S3_RESULTS_ACCESS_KEY,
    S3_RESULTS_BUCKET,
    S3_RESULTS_ENDPOINT,
    S3_RESULTS_REGION,
    S3_RESULTS_SECRET_KEY,
    S3_SECRET_KEY,
    WAREHOUSE_PATH,
    WORKER_AUTH_TOKEN,
    WORKER_MODE,
    WORKER_URL,
    is_s3_warehouse,
)
from app.database import async_session
from app.models import CatalogObject, Database, QueryJob, Schema, WorkerVM
from app.warehouse import catalog

_running_tasks: dict[str, asyncio.Task] = {}
_running_conns: dict[str, duckdb.DuckDBPyConnection] = {}
_remote_workers: dict[str, str] = {}  # job_id -> worker IP

_CREATE_VIEW_RE = re.compile(
    r"^\s*CREATE\s+(?:(OR\s+REPLACE)\s+)?VIEW\s+"
    r"(\w+)\.(\w+)\.(\w+)\s+AS\s+(.+)",
    re.IGNORECASE | re.DOTALL,
)


def _try_create_view(sql: str):
    """Parse a CREATE [OR REPLACE] VIEW db.schema.name AS ... statement.

    Returns (db_name, schema_name, view_name, select_sql, replace) or None.
    """
    m = _CREATE_VIEW_RE.match(sql)
    if not m:
        return None
    return (
        m.group(2),
        m.group(3),
        m.group(4),
        m.group(5).strip().rstrip(";"),
        m.group(1) is not None,
    )


async def _create_view(db_name: str, schema_name: str, view_name: str, select_sql: str, replace: bool):
    """Persist a view definition in the catalog (PostgreSQL)."""
    async with async_session() as session:
        # Look up database and schema
        db_result = await session.execute(
            select(Database).where(Database.name == db_name)
        )
        db = db_result.scalar_one_or_none()
        if db is None:
            raise ValueError(f"Database '{db_name}' not found")

        schema_result = await session.execute(
            select(Schema).where(Schema.database_id == db.id, Schema.name == schema_name)
        )
        schema = schema_result.scalar_one_or_none()
        if schema is None:
            raise ValueError(f"Schema '{db_name}.{schema_name}' not found")

        # Check for existing object with the same name
        existing_result = await session.execute(
            select(CatalogObject).where(
                CatalogObject.schema_id == schema.id,
                CatalogObject.name == view_name,
            )
        )
        existing = existing_result.scalar_one_or_none()

        if existing is not None:
            if not replace:
                raise ValueError(
                    f"Object '{db_name}.{schema_name}.{view_name}' already exists. "
                    "Use CREATE OR REPLACE VIEW to overwrite."
                )
            existing.view_sql = select_sql
        else:
            session.add(CatalogObject(
                schema_id=schema.id,
                name=view_name,
                object_type="view",
                view_sql=select_sql,
            ))

        await session.commit()


_DROP_VIEW_RE = re.compile(
    r"^\s*DROP\s+VIEW\s+(?:(IF\s+EXISTS)\s+)?"
    r"(\w+)\.(\w+)\.(\w+)\s*;?\s*$",
    re.IGNORECASE,
)


def _try_drop_view(sql: str):
    """Parse a DROP VIEW [IF EXISTS] db.schema.name statement.

    Returns (db_name, schema_name, view_name, if_exists) or None.
    """
    m = _DROP_VIEW_RE.match(sql)
    if not m:
        return None
    return (m.group(2), m.group(3), m.group(4), m.group(1) is not None)


async def _drop_view(db_name: str, schema_name: str, view_name: str, if_exists: bool):
    """Remove a view definition from the catalog (PostgreSQL)."""
    async with async_session() as session:
        db_result = await session.execute(
            select(Database).where(Database.name == db_name)
        )
        db = db_result.scalar_one_or_none()
        if db is None:
            if if_exists:
                return
            raise ValueError(f"Database '{db_name}' not found")

        schema_result = await session.execute(
            select(Schema).where(Schema.database_id == db.id, Schema.name == schema_name)
        )
        schema = schema_result.scalar_one_or_none()
        if schema is None:
            if if_exists:
                return
            raise ValueError(f"Schema '{db_name}.{schema_name}' not found")

        existing_result = await session.execute(
            select(CatalogObject).where(
                CatalogObject.schema_id == schema.id,
                CatalogObject.name == view_name,
            )
        )
        existing = existing_result.scalar_one_or_none()

        if existing is None:
            if if_exists:
                return
            raise ValueError(f"View '{db_name}.{schema_name}.{view_name}' does not exist")

        if existing.object_type != "view":
            raise ValueError(
                f"'{db_name}.{schema_name}.{view_name}' is a {existing.object_type}, not a view"
            )

        await session.execute(
            delete(CatalogObject).where(CatalogObject.id == existing.id)
        )
        await session.commit()


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


def _run_duckdb(sql: str, job_id: str, catalog_objects: list[dict]) -> int:
    """Run a SQL query via DuckDB against registered catalog objects. Returns row count."""
    temp_dir = os.path.join(RESULTS_PATH, f".tmp_{job_id}")
    os.makedirs(temp_dir, exist_ok=True)
    conn = duckdb.connect()
    conn.execute(f"SET temp_directory='{temp_dir}'")
    _running_conns[job_id] = conn
    try:
        conn.install_extension("iceberg")
        conn.load_extension("iceberg")

        if is_s3_warehouse():
            conn.install_extension("httpfs")
            conn.load_extension("httpfs")
            use_ssl = "true" if S3_ENDPOINT.startswith("https://") else "false"
            conn.execute(f"""
                CREATE SECRET (
                    TYPE S3,
                    KEY_ID '{S3_ACCESS_KEY}',
                    SECRET '{S3_SECRET_KEY}',
                    REGION '{S3_REGION}',
                    ENDPOINT '{S3_ENDPOINT.replace("http://", "").replace("https://", "")}',
                    URL_STYLE 'path',
                    USE_SSL {use_ssl}
                )
            """)

        # Register all catalog objects using native DuckDB three-part naming
        created_dbs: set[str] = set()
        created_schemas: set[str] = set()
        sorted_objects = sorted(catalog_objects, key=lambda o: 0 if o["object_type"] == "table" else 1)
        for obj in sorted_objects:
            db_name = obj["database"]
            schema_name = obj["schema"]

            if db_name not in created_dbs:
                conn.execute(f'ATTACH \':memory:\' AS "{db_name}"')
                created_dbs.add(db_name)

            full_schema = f'"{db_name}"."{schema_name}"'
            if full_schema not in created_schemas:
                conn.execute(f"CREATE SCHEMA IF NOT EXISTS {full_schema}")
                created_schemas.add(full_schema)

            qualified_name = f'{full_schema}."{obj["name"]}"'
            if obj["object_type"] == "table" and obj["iceberg_identifier"]:
                tbl = catalog.load_table(obj["iceberg_identifier"])
                metadata_path = tbl.metadata_location
                conn.execute(
                    f"CREATE VIEW {qualified_name} AS "
                    f"SELECT * FROM iceberg_scan('{metadata_path}')"
                )
            elif obj["object_type"] == "view" and obj["view_sql"]:
                try:
                    conn.execute(
                        f"CREATE VIEW {qualified_name} AS "
                        f"{obj['view_sql']}"
                    )
                except duckdb.Error:
                    pass  # skip broken views — they'll error only if actually queried

        # Strip trailing semicolons
        sql = sql.strip().rstrip(";").strip()

        # Append LIMIT only if user didn't already specify one
        if not re.search(r'\bLIMIT\s+\d+(\s+OFFSET\s+\d+)?\s*$', sql, re.IGNORECASE):
            sql = f"{sql} LIMIT {MAX_RESULT_ROWS}"

        result = conn.execute(sql)

        # Fetch as PyArrow and write to Parquet
        arrow_table = result.fetch_arrow_table()
        row_count = arrow_table.num_rows
        pq.write_table(arrow_table, _result_path(job_id))
        return row_count
    finally:
        _running_conns.pop(job_id, None)
        conn.close()
        shutil.rmtree(temp_dir, ignore_errors=True)


async def _update_job(job_id: str, **kwargs):
    async with async_session() as session:
        await session.execute(
            update(QueryJob).where(QueryJob.id == job_id).values(**kwargs)
        )
        await session.commit()


async def _execute_remote(job_id: str, sql: str, user_id: int):
    """Execute a query on a remote worker VM."""
    import httpx

    from app.worker_manager import ensure_worker, wait_for_ready

    # Load catalog objects and resolve metadata locations
    catalog_objects = await _load_catalog_objects()

    resolved_objects = []
    for obj in catalog_objects:
        entry = {
            "duckdb_schema": f"{obj['database']}.{obj['schema']}",
            "name": obj["name"],
            "object_type": obj["object_type"],
        }
        if obj["object_type"] == "table" and obj["iceberg_identifier"]:
            tbl = await asyncio.to_thread(catalog.load_table, obj["iceberg_identifier"])
            entry["metadata_location"] = tbl.metadata_location
        elif obj["object_type"] == "view" and obj["view_sql"]:
            entry["view_sql"] = obj["view_sql"]
        resolved_objects.append(entry)

    # Ensure worker VM is ready
    await _update_job(job_id, status="provisioning")
    vm = await ensure_worker(user_id)
    if vm.status == "provisioning":
        await wait_for_ready(vm.id)
        # Refresh VM to get updated status
        async with async_session() as session:
            result = await session.execute(
                select(WorkerVM).where(WorkerVM.id == vm.id)
            )
            vm = result.scalar_one()

    # Build S3 config for worker
    result_path = f"s3://{S3_RESULTS_BUCKET}/results/{job_id}.parquet"

    # POST query to worker
    payload = {
        "job_id": job_id,
        "sql": sql,
        "catalog_objects": resolved_objects,
        "s3": {
            "endpoint": S3_RESULTS_ENDPOINT,
            "access_key": S3_RESULTS_ACCESS_KEY,
            "secret_key": S3_RESULTS_SECRET_KEY,
            "region": S3_RESULTS_REGION,
            "result_path": result_path,
        },
        "max_result_rows": MAX_RESULT_ROWS,
    }
    headers = {"Authorization": f"Bearer {WORKER_AUTH_TOKEN}"}
    _remote_workers[job_id] = vm.private_ip

    # Mark VM as recently active so the idle reaper won't destroy it mid-query
    async with async_session() as session:
        await session.execute(
            update(WorkerVM).where(WorkerVM.id == vm.id).values(
                last_query_at=datetime.utcnow()
            )
        )
        await session.commit()

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            for attempt in range(3):
                try:
                    resp = await client.post(
                        f"http://{vm.private_ip}:8080/query",
                        json=payload,
                        headers=headers,
                    )
                    resp.raise_for_status()
                    break
                except httpx.TransportError:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(5)
            await _update_job(job_id, status="running")

        # Poll worker until done
        poll_count = 0
        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                await asyncio.sleep(2)
                resp = await client.get(
                    f"http://{vm.private_ip}:8080/query/{job_id}",
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                if data["status"] != "running":
                    break
                # Refresh last_query_at every ~60s to keep reaper away
                poll_count += 1
                if poll_count % 30 == 0:
                    async with async_session() as session:
                        await session.execute(
                            update(WorkerVM).where(WorkerVM.id == vm.id).values(
                                last_query_at=datetime.utcnow()
                            )
                        )
                        await session.commit()
    finally:
        _remote_workers.pop(job_id, None)

    # Update WorkerVM.last_query_at
    async with async_session() as session:
        await session.execute(
            update(WorkerVM).where(WorkerVM.id == vm.id).values(
                last_query_at=datetime.utcnow()
            )
        )
        await session.commit()

    if data["status"] == "cancelled":
        raise asyncio.CancelledError()
    if data["status"] == "failed":
        raise RuntimeError(data.get("error", "Remote query failed"))

    row_count = data.get("row_count", 0)
    return row_count


async def _execute_local(job_id: str, sql: str):
    """Execute a query locally via DuckDB (existing behavior)."""
    await _update_job(job_id, status="running")
    catalog_objects = await _load_catalog_objects()
    row_count = await asyncio.to_thread(_run_duckdb, sql, job_id, catalog_objects)
    return row_count


async def _execute_local_worker(job_id: str, sql: str):
    """Execute a query on a locally-running worker over HTTP."""
    import httpx

    catalog_objects = await _load_catalog_objects()

    resolved_objects = []
    for obj in catalog_objects:
        entry = {
            "duckdb_schema": f"{obj['database']}.{obj['schema']}",
            "name": obj["name"],
            "object_type": obj["object_type"],
        }
        if obj["object_type"] == "table" and obj["iceberg_identifier"]:
            tbl = await asyncio.to_thread(catalog.load_table, obj["iceberg_identifier"])
            entry["metadata_location"] = tbl.metadata_location
        elif obj["object_type"] == "view" and obj["view_sql"]:
            entry["view_sql"] = obj["view_sql"]
        resolved_objects.append(entry)

    result_path = _result_path(job_id)

    payload = {
        "job_id": job_id,
        "sql": sql,
        "catalog_objects": resolved_objects,
        "s3": {
            "endpoint": S3_ENDPOINT,
            "access_key": S3_ACCESS_KEY,
            "secret_key": S3_SECRET_KEY,
            "region": S3_REGION,
            "result_path": result_path,
        },
        "max_result_rows": MAX_RESULT_ROWS,
    }
    headers = {"Authorization": f"Bearer {WORKER_AUTH_TOKEN}"}
    _remote_workers[job_id] = WORKER_URL

    try:
        await _update_job(job_id, status="running")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{WORKER_URL}/query", json=payload, headers=headers,
            )
            resp.raise_for_status()

        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                await asyncio.sleep(2)
                resp = await client.get(
                    f"{WORKER_URL}/query/{job_id}", headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                if data["status"] != "running":
                    break
    finally:
        _remote_workers.pop(job_id, None)

    if data["status"] == "cancelled":
        raise asyncio.CancelledError()
    if data["status"] == "failed":
        raise RuntimeError(data.get("error", "Local worker query failed"))

    return data.get("row_count", 0)


async def execute_query(job_id: str, sql: str, user_id: int = 0):
    await _update_job(job_id, started_at=datetime.utcnow())
    try:
        # Intercept CREATE VIEW DDL — persist in catalog, skip DuckDB
        match = _try_create_view(sql)
        if match:
            db_name, schema_name, view_name, select_sql, replace = match
            await _create_view(db_name, schema_name, view_name, select_sql, replace)
            await _update_job(
                job_id,
                status="completed",
                row_count=0,
                completed_at=datetime.utcnow(),
            )
            return

        # Intercept DROP VIEW DDL
        drop_match = _try_drop_view(sql)
        if drop_match:
            db_name, schema_name, view_name, if_exists = drop_match
            await _drop_view(db_name, schema_name, view_name, if_exists)
            await _update_job(
                job_id,
                status="completed",
                row_count=0,
                completed_at=datetime.utcnow(),
            )
            return

        if WORKER_MODE == "remote":
            row_count = await _execute_remote(job_id, sql, user_id)
        elif WORKER_MODE == "local-worker":
            row_count = await _execute_local_worker(job_id, sql)
        else:
            row_count = await _execute_local(job_id, sql)

        await _update_job(
            job_id,
            status="completed",
            row_count=row_count,
            completed_at=datetime.utcnow(),
        )
    except asyncio.CancelledError:
        await _update_job(job_id, status="cancelled", completed_at=datetime.utcnow())
    except duckdb.InterruptException:
        await _update_job(job_id, status="cancelled", completed_at=datetime.utcnow())
    except Exception as e:
        await _update_job(
            job_id,
            status="failed",
            error=str(e)[:2048],
            completed_at=datetime.utcnow(),
        )
    finally:
        temp_dir = os.path.join(RESULTS_PATH, f".tmp_{job_id}")
        shutil.rmtree(temp_dir, ignore_errors=True)


async def cancel_query(job_id: str) -> bool:
    task = _running_tasks.get(job_id)
    if task is None:
        return False

    # For remote/local workers, send cancel to the worker first
    worker_addr = _remote_workers.get(job_id)
    if worker_addr:
        import httpx

        if worker_addr.startswith("http"):
            cancel_url = f"{worker_addr}/query/{job_id}/cancel"
        else:
            cancel_url = f"http://{worker_addr}:8080/query/{job_id}/cancel"
        headers = {"Authorization": f"Bearer {WORKER_AUTH_TOKEN}"}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(cancel_url, headers=headers)
        except Exception:
            pass  # Best effort — task.cancel() below will stop the polling loop

    # Interrupt the local DuckDB connection (for local mode)
    conn = _running_conns.get(job_id)
    if conn is not None:
        conn.interrupt()

    task.cancel()
    return True


def submit_query(job_id: str, sql: str, user_id: int = 0):
    task = asyncio.create_task(execute_query(job_id, sql, user_id))
    _running_tasks[job_id] = task

    def _on_done(_t):
        _running_tasks.pop(job_id, None)

    task.add_done_callback(_on_done)
