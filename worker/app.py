import asyncio
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

import duckdb

from config import WORKER_AUTH_TOKEN
from executor import cancel, execute_query
from sessions import session_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_reap_sessions())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _reap_sessions():
    while True:
        await asyncio.sleep(60)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, session_manager.cleanup_expired)


app = FastAPI(title="Kolkhis Worker", lifespan=lifespan)


# --- Auth dependency ---


def verify_token(authorization: Annotated[str, Header()]) -> None:
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    if authorization[len(prefix) :] != WORKER_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")


Authenticated = Annotated[None, Depends(verify_token)]


# --- Models ---


class S3Config(BaseModel):
    endpoint: str
    access_key: str
    secret_key: str
    region: str
    result_path: str


class CatalogObject(BaseModel):
    duckdb_schema: str
    name: str
    object_type: str
    metadata_location: str | None = None
    view_sql: str | None = None


class QueryRequest(BaseModel):
    job_id: str
    sql: str
    catalog_objects: list[CatalogObject]
    s3: S3Config
    max_result_rows: int = 100000


class QuerySubmitResponse(BaseModel):
    job_id: str


class QueryStatusResponse(BaseModel):
    status: str
    row_count: int | None = None
    error: str | None = None


# --- In-memory job tracking ---

_jobs: dict[str, dict] = {}


# --- Endpoints ---


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/query", response_model=QuerySubmitResponse)
async def submit_query(req: QueryRequest, _auth: Authenticated):
    if req.job_id in _jobs:
        raise HTTPException(status_code=409, detail="Job already exists")

    loop = asyncio.get_running_loop()
    task = loop.run_in_executor(
        None,
        execute_query,
        req.job_id,
        req.sql,
        [obj.model_dump() for obj in req.catalog_objects],
        req.s3.endpoint,
        req.s3.access_key,
        req.s3.secret_key,
        req.s3.region,
        req.s3.result_path,
        req.max_result_rows,
    )

    _jobs[req.job_id] = {"status": "running", "row_count": None, "error": None}

    async def _track(job_id: str, fut: asyncio.Future):
        try:
            row_count = await fut
            _jobs[job_id]["status"] = "completed"
            _jobs[job_id]["row_count"] = row_count
        except duckdb.InterruptException:
            _jobs[job_id]["status"] = "cancelled"
        except Exception as exc:
            _jobs[job_id]["status"] = "failed"
            _jobs[job_id]["error"] = str(exc)

    asyncio.create_task(_track(req.job_id, task))

    return QuerySubmitResponse(job_id=req.job_id)


@app.get("/query/{job_id}", response_model=QueryStatusResponse)
async def query_status(job_id: str, _auth: Authenticated):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return QueryStatusResponse(**job)


@app.post("/query/{job_id}/cancel")
async def cancel_query(job_id: str, _auth: Authenticated):
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    cancelled = cancel(job_id)
    if not cancelled:
        raise HTTPException(status_code=409, detail="Job not running or already finished")
    return {"status": "cancelling"}


# --- Session models ---


class SessionS3Config(BaseModel):
    endpoint: str
    access_key: str
    secret_key: str
    region: str


class CreateSessionRequest(BaseModel):
    catalog_objects: list[CatalogObject]
    s3: SessionS3Config


class SessionQueryRequest(BaseModel):
    sql: str
    fetch_results: bool = True


class ExportArrowRequest(BaseModel):
    table: str  # e.g. '"schema"."name"'


# --- Session endpoints ---


@app.post("/session")
async def create_session(req: CreateSessionRequest, _auth: Authenticated):
    loop = asyncio.get_running_loop()
    session_id = await loop.run_in_executor(
        None,
        session_manager.create,
        [obj.model_dump() for obj in req.catalog_objects],
        req.s3.endpoint,
        req.s3.access_key,
        req.s3.secret_key,
        req.s3.region,
    )
    return {"session_id": session_id}


@app.post("/session/{session_id}/query")
async def session_query(session_id: str, req: SessionQueryRequest, _auth: Authenticated):
    if session_manager.get(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, session_manager.execute, session_id, req.sql, req.fetch_results,
    )
    return result


@app.delete("/session/{session_id}")
async def close_session(session_id: str, _auth: Authenticated):
    if not session_manager.close(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "closed"}


@app.post("/session/{session_id}/export-arrow")
async def export_arrow(session_id: str, req: ExportArrowRequest, _auth: Authenticated):
    if session_manager.get(session_id) is None:
        raise HTTPException(status_code=404, detail="Session not found")
    loop = asyncio.get_running_loop()
    arrow_bytes = await loop.run_in_executor(
        None, session_manager.export_arrow, session_id, req.table,
    )
    return Response(content=arrow_bytes, media_type="application/vnd.apache.arrow.stream")


@app.post("/session/{session_id}/keepalive")
async def session_keepalive(session_id: str, _auth: Authenticated):
    if not session_manager.keepalive(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "ok"}
