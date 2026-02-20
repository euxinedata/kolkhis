from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import DATABASE_URL_ASYNC

engine = create_async_engine(DATABASE_URL_ASYNC)
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with async_session() as session:
        yield session
