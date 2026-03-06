import csv
import io
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.ddl import detect_ddl, execute_ddl, execute_show
from app.config import (
    RESULTS_PAGE_SIZE,
    S3_RESULTS_ACCESS_KEY,
    S3_RESULTS_BUCKET,
    S3_RESULTS_ENDPOINT,
    S3_RESULTS_REGION,
    S3_RESULTS_SECRET_KEY,
    WORKER_MODE,
)
from app.database import get_db
from app.models import QueryJob
from app.query_engine import _result_path, cancel_query, submit_query

router = APIRouter(prefix="/api/queries")


def _read_result_table(job_id: str):
    """Read a result Parquet table from local filesystem or S3."""
    import pyarrow.parquet as pq

    if WORKER_MODE == "remote":
        from pyarrow.fs import S3FileSystem

        endpoint = S3_RESULTS_ENDPOINT.replace("http://", "").replace("https://", "")
        use_ssl = S3_RESULTS_ENDPOINT.startswith("https://")
        fs = S3FileSystem(
            access_key=S3_RESULTS_ACCESS_KEY,
            secret_key=S3_RESULTS_SECRET_KEY,
            region=S3_RESULTS_REGION,
            endpoint_override=endpoint,
            scheme="https" if use_ssl else "http",
        )
        path = f"{S3_RESULTS_BUCKET}/results/{job_id}.parquet"
        return pq.read_table(path, filesystem=fs)
    else:
        return pq.read_table(_result_path(job_id))


class SubmitQuery(BaseModel):
    sql: str


@router.post("")
async def create_query(
    body: SubmitQuery,
    user: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    from datetime import datetime

    org_id = user.get("org_id", "")

    # Intercept DDL statements (CREATE DATABASE, CREATE SCHEMA, etc.)
    try:
        ddl = detect_ddl(body.sql)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if ddl is not None:
        job_id = str(uuid.uuid4())
        try:
            if ddl["op"].startswith("show_"):
                row_count = await execute_show(ddl, org_id, job_id, db)
                job = QueryJob(
                    id=job_id, user_id=int(user["sub"]), sql=body.sql,
                    status="completed", row_count=row_count,
                    started_at=datetime.utcnow(), completed_at=datetime.utcnow(),
                )
                db.add(job)
                await db.commit()
                return {"job_id": job_id}
            else:
                message = await execute_ddl(ddl, org_id, db)
                job = QueryJob(
                    id=job_id, user_id=int(user["sub"]), sql=body.sql,
                    status="completed", row_count=0,
                    started_at=datetime.utcnow(), completed_at=datetime.utcnow(),
                )
                db.add(job)
                await db.commit()
                return {"job_id": job_id, "ddl_message": message}
        except ValueError as e:
            job = QueryJob(
                id=job_id, user_id=int(user["sub"]), sql=body.sql,
                status="failed", error=str(e),
                started_at=datetime.utcnow(), completed_at=datetime.utcnow(),
            )
            db.add(job)
            await db.commit()
            raise HTTPException(status_code=400, detail=str(e))

    job_id = str(uuid.uuid4())
    job = QueryJob(id=job_id, user_id=int(user["sub"]), sql=body.sql, status="pending")
    db.add(job)
    await db.commit()
    submit_query(job_id, body.sql, user_id=int(user["sub"]), org_id=org_id)
    return {"job_id": job_id}


@router.get("")
async def list_queries(
    user: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(QueryJob)
        .where(QueryJob.user_id == int(user["sub"]))
        .order_by(QueryJob.created_at.desc())
    )
    jobs = result.scalars().all()
    return [
        {
            "id": j.id,
            "sql": j.sql,
            "status": j.status,
            "error": j.error,
            "row_count": j.row_count,
            "started_at": j.started_at.isoformat() if j.started_at else None,
            "completed_at": j.completed_at.isoformat() if j.completed_at else None,
            "created_at": j.created_at.isoformat() if j.created_at else None,
        }
        for j in jobs
    ]


@router.get("/{job_id}")
async def get_query(
    job_id: str,
    user: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(QueryJob).where(
            QueryJob.id == job_id, QueryJob.user_id == int(user["sub"])
        )
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Query not found")
    return {
        "id": job.id,
        "sql": job.sql,
        "status": job.status,
        "error": job.error,
        "row_count": job.row_count,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }


@router.post("/{job_id}/cancel")
async def cancel_query_endpoint(
    job_id: str,
    user: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(QueryJob).where(
            QueryJob.id == job_id, QueryJob.user_id == int(user["sub"])
        )
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Query not found")
    if job.status not in ("pending", "running"):
        raise HTTPException(status_code=400, detail=f"Query is already {job.status}")
    cancelled = await cancel_query(job_id)
    if not cancelled:
        # Task already finished — update DB directly
        from datetime import datetime
        await db.execute(
            update(QueryJob).where(QueryJob.id == job_id).values(
                status="cancelled", completed_at=datetime.utcnow()
            )
        )
        await db.commit()
    return {"status": "cancelled"}


@router.get("/{job_id}/results")
async def get_results(
    job_id: str,
    page: int = 0,
    user: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(QueryJob).where(
            QueryJob.id == job_id, QueryJob.user_id == int(user["sub"])
        )
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Query not found")
    if job.status != "completed":
        raise HTTPException(status_code=400, detail=f"Query status: {job.status}")

    table = _read_result_table(job_id)
    total = table.num_rows
    start = page * RESULTS_PAGE_SIZE
    end = min(start + RESULTS_PAGE_SIZE, total)
    sliced = table.slice(start, end - start)

    columns = [field.name for field in sliced.schema]
    rows = sliced.to_pydict()
    # Convert column-oriented dict to row-oriented list
    row_list = []
    for i in range(sliced.num_rows):
        row_list.append({col: rows[col][i] for col in columns})

    return {
        "columns": columns,
        "rows": row_list,
        "total": total,
        "page": page,
        "page_size": RESULTS_PAGE_SIZE,
    }


@router.get("/{job_id}/export")
async def export_csv(
    job_id: str,
    user: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(QueryJob).where(
            QueryJob.id == job_id, QueryJob.user_id == int(user["sub"])
        )
    )
    job = result.scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Query not found")
    if job.status != "completed":
        raise HTTPException(status_code=400, detail=f"Query status: {job.status}")

    table = _read_result_table(job_id)
    columns = [field.name for field in table.schema]
    rows = table.to_pydict()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(columns)
    for i in range(table.num_rows):
        writer.writerow([rows[col][i] for col in columns])

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={job_id}.csv"},
    )
