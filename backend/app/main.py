import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from app.config import (
    JWT_SECRET, FRONTEND_URL, RESULTS_PATH, HOMES_PATH, WAREHOUSE_PATH,
    WORKER_MODE, GITEA_ADMIN_PASSWORD, is_s3_warehouse,
)
from app.database import engine, async_session, get_db
from app.models import Base, Country
from app.seed import seed_catalog, seed_countries, seed_server_type_rates
from app.auth import router as auth_router, verify_token
from app.routers.billing import router as billing_router
from app.routers.catalog import router as catalog_router
from app.routers.queries import router as queries_router
from app.routers.settings import router as settings_router
from app.routers.projects import router as projects_router
from app.routers.terminal import router as terminal_router
from app.routers.workers import router as workers_router
from app.routers.dbt import router as dbt_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(RESULTS_PATH, exist_ok=True)
    os.makedirs(HOMES_PATH, exist_ok=True)
    if not is_s3_warehouse():
        os.makedirs(WAREHOUSE_PATH, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        await seed_countries(session)
    async with async_session() as session:
        await seed_server_type_rates(session)
    async with async_session() as session:
        await seed_catalog(session)

    if GITEA_ADMIN_PASSWORD:
        from app.gitea import bootstrap_token
        try:
            await bootstrap_token()
        except Exception as exc:
            logging.getLogger(__name__).warning("Gitea bootstrap failed: %s", exc)

    reaper_task = None
    if WORKER_MODE == "remote":
        from app.worker_manager import cleanup_stale_workers, idle_reaper
        await cleanup_stale_workers()
        reaper_task = asyncio.create_task(idle_reaper())

    yield

    if reaper_task is not None:
        reaper_task.cancel()
        try:
            await reaper_task
        except asyncio.CancelledError:
            pass
    await engine.dispose()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SessionMiddleware, secret_key=JWT_SECRET)

app.include_router(auth_router)
app.include_router(billing_router)
app.include_router(catalog_router)
app.include_router(projects_router)
app.include_router(queries_router)
app.include_router(settings_router)
app.include_router(terminal_router)
app.include_router(workers_router)
app.include_router(dbt_router)


_UNAUTH = JSONResponse({"detail": "Not authenticated"}, status_code=401)


@app.get("/openapi.json", include_in_schema=False)
async def openapi_json(request: Request):
    if verify_token(request) is None:
        return _UNAUTH
    return JSONResponse(app.openapi())


@app.get("/docs", include_in_schema=False)
async def docs(request: Request):
    if verify_token(request) is None:
        return _UNAUTH
    return get_swagger_ui_html(openapi_url="/openapi.json", title="docs")


@app.get("/redoc", include_in_schema=False)
async def redoc(request: Request):
    if verify_token(request) is None:
        return _UNAUTH
    return get_redoc_html(openapi_url="/openapi.json", title="redoc")


@app.get("/health")
async def health():
    async with async_session() as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}


@app.get("/countries")
async def list_countries(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Country).order_by(Country.name))
    countries = result.scalars().all()
    return [
        {"name": c.name, "alpha_2": c.alpha_2, "alpha_3": c.alpha_3}
        for c in countries
    ]
