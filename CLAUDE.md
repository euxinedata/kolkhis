# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Kolkhis is a full-stack web application with a Python/FastAPI backend and a React/TypeScript frontend, backed by PostgreSQL.

## Architecture

- **`backend/`** — FastAPI app with async SQLAlchemy (asyncpg driver) and Alembic migrations
  - `app/main.py` — FastAPI app with lifespan handler (auto-creates tables and seeds data on startup)
  - `app/models.py` — SQLAlchemy ORM models using `DeclarativeBase` and `Mapped` typed columns
  - `app/database.py` — Async engine and session factory; DB URL from `DATABASE_URL` env var (defaults to `postgresql+asyncpg://kolkhis:kolkhis-dev-2026@localhost:5432/kolkhis`)
  - `app/seed.py` — Seeds countries table from `pycountry` on first run
  - `alembic/` — Migration scripts; `env.py` reads `DATABASE_URL` (uses `psycopg2` sync driver for migrations)
- **`frontend/`** — React 19 + TypeScript + Vite 7 (currently default Vite template, not yet connected to backend)

## Commands

### Backend

```bash
# From backend/ directory
pip install -r requirements.txt          # Install dependencies
uvicorn app.main:app --reload            # Run dev server (default port 8000)

# Alembic migrations (from backend/)
alembic revision --autogenerate -m "description"   # Generate migration
alembic upgrade head                                # Apply migrations
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

PostgreSQL is required. Default connection: `kolkhis:kolkhis-dev-2026@localhost:5432/kolkhis`. Override with `DATABASE_URL` env var. The app auto-creates tables and seeds country data on startup, so Alembic migrations are optional for dev.
