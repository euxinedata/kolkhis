---
name: backend
description: Backend development conventions for Kolkhis (FastAPI + SQLAlchemy + Iceberg + DuckDB)
user-invocable: false
---

# Backend Development Conventions

## Router Pattern

Routers live in `backend/app/routers/<resource>.py`. Every router:

```python
from fastapi import APIRouter, Depends
from app.auth import require_auth

router = APIRouter(prefix="/api/<resource>")

@router.get("")
async def list_items(_user: dict = Depends(require_auth)):
    ...
```

- Prefix: `/api/<resource>` (plural)
- All endpoints use `Depends(require_auth)` — name the param `_user` if unused, `user` if needed
- Mount in `app/main.py`: `app.include_router(router)`

## Pydantic Models

Define request/response bodies as Pydantic `BaseModel` classes in the router file:

```python
from pydantic import BaseModel

class CreateItem(BaseModel):
    name: str
```

## SQLAlchemy Models

Models live in `backend/app/models.py`. Pattern:

```python
from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models import Base

class MyModel(Base):
    __tablename__ = "my_models"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
```

- Use `DeclarativeBase` (imported as `Base` from `app.models`)
- Use `Mapped` typed columns with `mapped_column()`
- Timestamps use `server_default=func.now()`
- Optional fields: `Mapped[Optional[str]]` with `nullable=True`

## Database Access

Two patterns for async sessions:

```python
# 1. Dependency injection (in endpoints)
from app.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession

@router.get("")
async def list_items(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(MyModel))
    ...

# 2. Context manager (in background tasks / startup)
from app.database import async_session

async with async_session() as session:
    await session.execute(...)
    await session.commit()
```

## Iceberg Catalog

The PyIceberg SQL catalog is in `app/warehouse.py`:

```python
from app.warehouse import catalog

# List namespaces/tables
catalog.list_namespaces()        # returns list of tuples
catalog.list_tables("namespace") # returns list of tuples

# Load a table
tbl = catalog.load_table("namespace.table")
schema = tbl.schema()
```

## DuckDB Query Engine

`app/query_engine.py` runs SQL against Iceberg tables via DuckDB:
- Registers all Iceberg tables as DuckDB views at query time
- Runs queries in a thread pool (`asyncio.to_thread`)
- Writes results as Parquet to `RESULTS_PATH`
- Updates `QueryJob` status in PostgreSQL

## Configuration

All config in `backend/app/config.py`. Pattern:

```python
import os
MY_SETTING = os.environ.get("MY_SETTING", "default_value")
```

Required env vars (no default): `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `JWT_SECRET`
Optional env vars (with defaults): `FRONTEND_URL`, `POSTGRES_*`, `WAREHOUSE_PATH`, `RESULTS_PATH`, `MAX_RESULT_ROWS`, `RESULTS_PAGE_SIZE`

## Key Files

- `backend/app/main.py` — App setup, lifespan, router mounting
- `backend/app/models.py` — All SQLAlchemy models
- `backend/app/auth.py` — Google OAuth + JWT auth, `require_auth` dependency
- `backend/app/config.py` — Environment variables
- `backend/app/database.py` — Async engine and session factory
- `backend/app/warehouse.py` — PyIceberg catalog singleton
- `backend/app/query_engine.py` — DuckDB query execution
- `backend/app/routers/catalog.py` — Iceberg namespace/table CRUD
- `backend/app/routers/queries.py` — Query submission, status, results
