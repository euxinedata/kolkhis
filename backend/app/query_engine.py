import asyncio
import os
from datetime import datetime

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import update

from app.config import MAX_RESULT_ROWS, RESULTS_PATH, WAREHOUSE_PATH
from app.database import async_session
from app.models import QueryJob
from app.warehouse import catalog

_running_tasks: dict[str, asyncio.Task] = {}


def _result_path(job_id: str) -> str:
    return os.path.join(RESULTS_PATH, f"{job_id}.parquet")


def _run_duckdb(sql: str, job_id: str) -> int:
    """Run a SQL query via DuckDB against Iceberg tables. Returns row count."""
    conn = duckdb.connect()
    try:
        conn.install_extension("iceberg")
        conn.load_extension("iceberg")

        # Register all Iceberg tables as DuckDB views
        for ns_tuple in catalog.list_namespaces():
            ns = ns_tuple[0]
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS \"{ns}\"")
            for tbl_tuple in catalog.list_tables(ns):
                tbl_name = tbl_tuple[1]
                tbl = catalog.load_table(f"{ns}.{tbl_name}")
                metadata_path = tbl.metadata_location
                conn.execute(
                    f'CREATE VIEW "{ns}"."{tbl_name}" AS '
                    f"SELECT * FROM iceberg_scan('{metadata_path}')"
                )

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
        row_count = await asyncio.to_thread(_run_duckdb, sql, job_id)
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
