# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Kolkhis is a full-stack web application with a Python/FastAPI backend and a React/TypeScript frontend, backed by PostgreSQL. It provides a SQL query interface over Iceberg data lakehouse tables stored in S3-compatible object storage.

## Architecture

- **`backend/`** — FastAPI app with async SQLAlchemy (asyncpg driver) and Alembic migrations
  - `app/main.py` — FastAPI app with lifespan handler (auto-creates tables and seeds data on startup)
  - `app/models.py` — SQLAlchemy ORM models using `DeclarativeBase` and `Mapped` typed columns
  - `app/database.py` — Async engine and session factory
  - `app/config.py` — All configuration, loaded from project root `.env` file
  - `app/query_engine.py` — Query execution engine with three worker modes (see below)
  - `app/worker_manager.py` — Hetzner VM provisioning (remote mode only)
  - `app/seed.py` — Seeds countries table from `pycountry` on first run
  - `alembic/` — Migration scripts; `env.py` reads `DATABASE_URL` (uses `psycopg2` sync driver for migrations)
- **`worker/`** — Standalone DuckDB query worker service (FastAPI)
  - `app.py` — HTTP endpoints: `POST /query`, `GET /query/{id}`, `POST /query/{id}/cancel`
  - `executor.py` — DuckDB execution: registers Iceberg tables via S3, writes results to parquet
  - `config.py` — Worker config, loaded from project root `.env` file
- **`frontend/`** — React 19 + TypeScript + Vite 7

## Worker Modes

Controlled by `WORKER_MODE` in `.env`:

- **`local`** — DuckDB runs in-process within the backend. Simplest setup, no separate worker needed.
- **`local-worker`** — Backend sends queries over HTTP to a locally-running worker app. Same protocol as remote, but no VM provisioning. Worker reads Iceberg data from the same S3/MinIO as the backend. Results are written to local filesystem.
- **`remote`** — Backend provisions Hetzner VMs, sends queries over HTTP to the worker running on the VM. Results are written to S3.

### Local Worker Setup

1. Set in `.env`:
   ```
   WORKER_MODE=local-worker
   WORKER_URL=http://localhost:8080
   WORKER_AUTH_TOKEN=<any shared token>
   ```
2. Start the worker: `cd worker && uvicorn app:app --port 8080`
3. Start the backend: `cd backend && uvicorn app.main:app --reload`

The worker uses the warehouse S3 config (`S3_ENDPOINT`, `S3_ACCESS_KEY`, etc.) to read Iceberg table data. For local development, this points to MinIO.

### Remote Worker Setup

Uncomment the remote configuration block in `.env` (`WORKER_MODE=remote`, `HCLOUD_TOKEN`, `WORKER_SNAPSHOT_ID`, etc.) and the Hetzner Object Storage S3 config. Comment out the local worker and local MinIO blocks.

## Commands

### Backend

```bash
# From backend/ directory
uv sync                                 # Install dependencies
uvicorn app.main:app --reload            # Run dev server (default port 8000)

# Alembic migrations (from backend/)
alembic revision --autogenerate -m "description"   # Generate migration
alembic upgrade head                                # Apply migrations
```

### Worker

```bash
# From worker/ directory
uv sync                                 # Install dependencies
uvicorn app:app --port 8080              # Run worker (for local-worker mode)
```

### Frontend

```bash
# From frontend/ directory
npm install           # Install dependencies
npm run dev           # Vite dev server with HMR
npm run build         # TypeScript check + Vite production build
npm run lint          # ESLint
```

## Database

PostgreSQL is required. Default connection: `euxine:very_secure_password@localhost:5437/euxine`. Override individual parts with `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB` env vars. The app auto-creates tables and seeds country data on startup, so Alembic migrations are optional for dev.

The `iceberg_tables` and `iceberg_namespace_properties` tables are managed by PyIceberg's SQL catalog and store Iceberg table metadata locations. The `catalog_objects`, `databases`, and `schemas` tables map the user-facing three-part naming (`database.schema.table`) to Iceberg identifiers.

## Object Storage

The Iceberg warehouse lives in S3-compatible storage. Configured via `WAREHOUSE_PATH`, `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION` in `.env`.

- **Local development**: MinIO at `localhost:9000`, bucket `warehouse`
- **Production**: Hetzner Object Storage at `nbg1.your-objectstorage.com`, bucket `pontus-dev-iceberg`

When switching between local and remote, the `iceberg_tables` rows in PostgreSQL must be updated to point at the correct S3 metadata locations.

## Git Workflow

Feature branch flow on `main`:

1. **Start**: ensure local `main` is up to date (`git pull`)
2. **Branch**: create a feature branch off `main` (`git checkout -b feature/<short-name>`)
3. **Develop**: commit work on the feature branch. One-liner commit messages only — no multi-line bodies.
4. **Push**: push the feature branch to GitHub (`git push -u origin feature/<short-name>`)
5. **PR**: create a pull request on GitHub to merge into `main`
6. **Merge**: merge the PR on GitHub
7. **Cleanup**: delete the remote branch, then locally switch to `main` and pull (`git checkout main && git pull`), delete the local feature branch (`git branch -d feature/<short-name>`)

- **Commit messages**: one-liner, imperative mood. Examples: `Add query history page`, `Fix polling cleanup on unmount`
- **No direct pushes to `main`** — always go through a PR
- **No force pushes** to `main`
