---
name: architect
description: Full-stack architecture overview and design guidance for Kolkhis
user-invocable: true
---

# Software Architect

When invoked, analyze the current task or question from an architectural perspective. Consider the full stack and how components interact.

## System Overview

Kolkhis is a data warehouse application with four layers:

```
┌─────────────────────────────────────────────┐
│  Frontend (React 19 + Vite 7)               │
│  SPA served by nginx, talks to backend API  │
├─────────────────────────────────────────────┤
│  Backend API (FastAPI + async SQLAlchemy)    │
│  JWT auth, REST endpoints under /api/*      │
├──────────────────┬──────────────────────────┤
│  PostgreSQL      │  Iceberg Warehouse       │
│  Users, jobs,    │  Data tables (Parquet)    │
│  OAuth state,    │  PyIceberg SQL catalog    │
│  Iceberg catalog │  (metadata in Postgres)   │
├──────────────────┴──────────────────────────┤
│  DuckDB (query engine, ephemeral per query) │
│  Reads Iceberg metadata → scans Parquet     │
└─────────────────────────────────────────────┘
```

## Data Flow Paths

### Authentication
```
Browser → GET /auth/login/google → Google OAuth → GET /auth/callback/google
  → upsert User in PostgreSQL → set JWT cookie → redirect to frontend
Frontend → GET /auth/me (cookie) → verify JWT → return user info
All /api/* endpoints → require_auth dependency → decode JWT from cookie
```

### Query Execution
```
Frontend POST /api/queries {sql} → create QueryJob (pending) in PostgreSQL
  → asyncio.create_task → background thread:
    DuckDB connects → registers all Iceberg tables as views
    → executes SQL → writes result Parquet to RESULTS_PATH
    → updates QueryJob status in PostgreSQL
Frontend polls GET /api/queries/{id} until completed/failed
Frontend GET /api/queries/{id}/results?page=N → reads result Parquet → paginated JSON
```

### Catalog Management
```
Frontend → /api/catalog/* → PyIceberg catalog (backed by PostgreSQL + Parquet files)
  Namespaces and tables stored in PostgreSQL catalog tables
  Actual data stored as Parquet files under WAREHOUSE_PATH
```

## Component Boundaries

| Concern | Owner | Storage |
|---------|-------|---------|
| User accounts, sessions | `app/auth.py` | PostgreSQL `users` table |
| Query jobs (status, metadata) | `app/routers/queries.py` | PostgreSQL `query_jobs` table |
| Query execution | `app/query_engine.py` | DuckDB (ephemeral) → Parquet results |
| Data catalog (namespaces, tables) | `app/routers/catalog.py` | PyIceberg catalog in PostgreSQL |
| Data storage | Iceberg | Parquet files under `WAREHOUSE_PATH` |
| Frontend state | React hooks | Browser memory |
| Auth tokens | JWT in httponly cookie | Browser cookies |

## Decision Guidelines

When adding a new feature, consider:

1. **Where does the data live?** Operational data (users, jobs) → PostgreSQL. Analytical data → Iceberg/Parquet.
2. **Sync or async?** Short operations can be synchronous endpoints. Long-running operations (queries, data loads) should use the background task pattern from `query_engine.py`.
3. **Frontend or backend logic?** Validation and business logic belong in the backend. The frontend handles presentation and polling.
4. **New router or extend existing?** New resource type → new router in `app/routers/`. Extension of existing resource → add endpoints to existing router.
5. **Schema changes?** New PostgreSQL tables → add model to `models.py`, they auto-create on startup. New Iceberg tables → use the catalog API or a loading script.
6. **Auth boundary?** All `/api/*` endpoints require auth. Public endpoints (health, auth flow) live outside `/api/` prefix.

## Key Integration Points

- `app/warehouse.py` is the single PyIceberg catalog instance — used by both `catalog.py` router and `query_engine.py`
- `app/database.py` provides both `get_db` (dependency) and `async_session()` (context manager) — never create engines elsewhere
- `app/config.py` is the single source for all environment configuration
- Frontend `api.ts` is the single point for backend communication — never use raw `fetch` except in `auth.tsx`
