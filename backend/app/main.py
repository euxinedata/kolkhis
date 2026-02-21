import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html, get_redoc_html
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware

from app.config import JWT_SECRET, FRONTEND_URL, RESULTS_PATH, WAREHOUSE_PATH
from app.database import engine, async_session, get_db
from app.models import Base, Country
from app.seed import seed_countries
from app.auth import router as auth_router, verify_token
from app.routers.catalog import router as catalog_router
from app.routers.queries import router as queries_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(RESULTS_PATH, exist_ok=True)
    os.makedirs(WAREHOUSE_PATH, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        await seed_countries(session)
    yield
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
app.include_router(catalog_router)
app.include_router(queries_router)


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
