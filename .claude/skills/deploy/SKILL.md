---
name: deploy
description: Build and deploy Kolkhis
user-invocable: true
---

# Deployment

## Local Development

### PostgreSQL

```bash
docker compose up -d db
```

Uses `docker-compose.yml` with Postgres 17. Port configured via `POSTGRES_PORT` (default 5437).

### Backend

```bash
cd backend && uv run uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend && npm run dev
```

## Docker Build

### Backend

```bash
docker build -t kolkhis-backend backend/
```

- Base: `python:3.12-slim`
- Uses `uv` for dependency management
- Runs: `uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers`

### Frontend

```bash
docker build -t kolkhis-frontend frontend/
```

- Multi-stage: `node:22-slim` build, `nginx:alpine` serve
- Builds with `npm run build`, serves from nginx
- Requires `frontend/nginx.conf` for SPA routing

## Required Environment Variables

**Secrets (no defaults — must be set):**
- `GOOGLE_CLIENT_ID` — Google OAuth client ID
- `GOOGLE_CLIENT_SECRET` — Google OAuth client secret
- `JWT_SECRET` — Secret for signing JWT tokens

**Configurable (with defaults):**
- `FRONTEND_URL` — Frontend origin for CORS (default: `http://localhost:5173`)
- `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_DB`
- `WAREHOUSE_PATH` — Iceberg warehouse storage (default: `/mnt/warehouse`)
- `RESULTS_PATH` — Query result Parquet files (default: `/tmp/warehouse-results`)
- `MAX_RESULT_ROWS` — Row limit per query (default: `100000`)
- `RESULTS_PAGE_SIZE` — Rows per page in results API (default: `100`)
- `VITE_API_URL` — Backend URL for frontend (default: `https://api.euxine.eu`)

## Volume Mounts

Production containers need:
- `WAREHOUSE_PATH` — Persistent storage for Iceberg data files
- `RESULTS_PATH` — Storage for query result Parquet files (can be ephemeral)
- PostgreSQL data volume

## Key Files

- `backend/Dockerfile` — Backend container
- `frontend/Dockerfile` — Frontend container (multi-stage with nginx)
- `docker-compose.yml` — Local PostgreSQL
- `.env` — Environment variables (not committed)
