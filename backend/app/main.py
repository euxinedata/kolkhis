from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import engine, async_session, get_db
from app.models import Base, Country
from app.seed import seed_countries


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session() as session:
        await seed_countries(session)
    yield
    await engine.dispose()


app = FastAPI(lifespan=lifespan)


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
