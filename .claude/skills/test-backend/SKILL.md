---
name: test-backend
description: Run and write backend tests for Kolkhis
user-invocable: true
---

# Backend Testing

## Running Tests

```bash
cd backend && uv run pytest
```

## Test Location

Tests live in `backend/tests/`. File naming: `test_<module>.py`.

## Test Setup

### Dependencies

Add to `backend/pyproject.toml` dev dependencies:
```
pytest
pytest-asyncio
httpx
```

### conftest.py

Create `backend/tests/conftest.py` with shared fixtures:

```python
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from app.main import app
from app.models import Base
from app.auth import require_auth
from app.database import get_db

TEST_DB_URL = "sqlite+aiosqlite:///test.db"

@pytest.fixture
async def db_session():
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()

@pytest.fixture
def mock_user():
    return {"sub": "1", "email": "test@example.com", "name": "Test User"}

@pytest.fixture
async def client(db_session, mock_user):
    async def override_db():
        yield db_session

    async def override_auth():
        return mock_user

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_auth] = override_auth
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()
```

## Writing Tests

```python
import pytest

@pytest.mark.asyncio
async def test_list_queries(client):
    response = await client.get("/api/queries")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

@pytest.mark.asyncio
async def test_create_query(client):
    response = await client.post("/api/queries", json={"sql": "SELECT 1"})
    assert response.status_code == 200
    assert "job_id" in response.json()
```

## Conventions

- Use `@pytest.mark.asyncio` for all async test functions
- Override `require_auth` to bypass Google OAuth in tests
- Override `get_db` to use test database
- Use `httpx.AsyncClient` with `ASGITransport` for endpoint tests
- Test both success and error paths (401, 404, 400)
