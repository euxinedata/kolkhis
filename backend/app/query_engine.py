import asyncio
import os
from datetime import datetime

from sqlalchemy import select, update

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
    WORKER_AUTH_TOKEN,
    WORKER_MODE,
    WORKER_URL,
)
from app.database import async_session
from app.models import OrgDatabase, QueryJob, WorkerVM
from app.sql_rewriter import rewrite
from app.warehouse import get_database_catalog

_running_tasks: dict[str, asyncio.Task] = {}
_remote_workers: dict[str, str] = {}  # job_id -> worker IP


def _result_path(job_id: str) -> str:
    return os.path.join(RESULTS_PATH, f"{job_id}.parquet")


def _load_iceberg_tables(org_databases: list[OrgDatabase]) -> list[dict]:
    """Enumerate all tables across all org databases."""
    tables = []
    for org_db in org_databases:
        catalog = get_database_catalog(org_db.lakekeeper_warehouse)
        for ns in catalog.list_namespaces():
            schema_name = ns[0]
            for table_id in catalog.list_tables(schema_name):
                tables.append({
                    "database": org_db.name,
                    "schema": schema_name,
                    "name": table_id[-1],
                    "lakekeeper_warehouse": org_db.lakekeeper_warehouse,
                })
    return tables


async def _update_job(job_id: str, **kwargs):
    async with async_session() as session:
        await session.execute(
            update(QueryJob).where(QueryJob.id == job_id).values(**kwargs)
        )
        await session.commit()


async def _execute_remote(job_id: str, sql: str, user_id: int, org_id: str):
    """Execute a query on a remote worker VM."""
    import httpx

    from app.worker_manager import ensure_worker, wait_for_ready

    # Load org databases and enumerate tables
    async with async_session() as session:
        result = await session.execute(
            select(OrgDatabase).where(OrgDatabase.org_id == org_id)
        )
        org_databases = list(result.scalars().all())

    iceberg_tables = await asyncio.to_thread(_load_iceberg_tables, org_databases)

    resolved_objects = []
    for tbl_info in iceberg_tables:
        catalog = get_database_catalog(tbl_info["lakekeeper_warehouse"])
        tbl = await asyncio.to_thread(
            catalog.load_table, f"{tbl_info['schema']}.{tbl_info['name']}"
        )
        resolved_objects.append({
            "duckdb_schema": f"{tbl_info['database']}.{tbl_info['schema']}",
            "name": tbl_info["name"],
            "object_type": "table",
            "metadata_location": tbl.metadata_location,
        })

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


async def _execute_local_worker(job_id: str, sql: str, org_id: str):
    """Execute a query on a locally-running worker over HTTP."""
    import httpx

    async with async_session() as session:
        result = await session.execute(
            select(OrgDatabase).where(OrgDatabase.org_id == org_id)
        )
        org_databases = list(result.scalars().all())

    iceberg_tables = await asyncio.to_thread(_load_iceberg_tables, org_databases)

    resolved_objects = []
    for tbl_info in iceberg_tables:
        catalog = get_database_catalog(tbl_info["lakekeeper_warehouse"])
        tbl = await asyncio.to_thread(
            catalog.load_table, f"{tbl_info['schema']}.{tbl_info['name']}"
        )
        resolved_objects.append({
            "duckdb_schema": f"{tbl_info['database']}.{tbl_info['schema']}",
            "name": tbl_info["name"],
            "object_type": "table",
            "metadata_location": tbl.metadata_location,
        })

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


async def execute_query(job_id: str, sql: str, user_id: int = 0, org_id: str = ""):
    sql = rewrite(sql)
    await _update_job(job_id, started_at=datetime.utcnow())
    try:
        if WORKER_MODE == "remote":
            row_count = await _execute_remote(job_id, sql, user_id, org_id)
        else:
            row_count = await _execute_local_worker(job_id, sql, org_id)

        await _update_job(
            job_id,
            status="completed",
            row_count=row_count,
            completed_at=datetime.utcnow(),
        )
    except asyncio.CancelledError:
        await _update_job(job_id, status="cancelled", completed_at=datetime.utcnow())
    except Exception as e:
        await _update_job(
            job_id,
            status="failed",
            error=str(e)[:2048],
            completed_at=datetime.utcnow(),
        )


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

    task.cancel()
    return True


def submit_query(job_id: str, sql: str, user_id: int = 0, org_id: str = ""):
    task = asyncio.create_task(execute_query(job_id, sql, user_id, org_id))
    _running_tasks[job_id] = task

    def _on_done(_t):
        _running_tasks.pop(job_id, None)

    task.add_done_callback(_on_done)
