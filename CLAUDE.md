# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Kolkhis is a full-stack web application with a Python/FastAPI backend and a React/TypeScript frontend, backed by PostgreSQL. It provides a SQL query interface over Iceberg data lakehouse tables stored in S3-compatible object storage.

## Architecture

- **`backend/`** — FastAPI app with async SQLAlchemy (asyncpg driver) and Alembic migrations
  - `app/main.py` — FastAPI app with lifespan handler (creates tables, seeds data, bootstraps Gitea token, manages workers on startup)
  - `app/models.py` — SQLAlchemy ORM models using `DeclarativeBase` and `Mapped` typed columns
  - `app/database.py` — Async engine, session factory, and `get_db` FastAPI dependency
  - `app/config.py` — All configuration, loaded from project root `.env` file
  - `app/auth.py` — Google OAuth login, JWT token management, cookie handling
  - `app/billing.py` — Billing summary computation from usage events and server type rates
  - `app/warehouse.py` — PyIceberg `SqlCatalog` initialization with S3 config
  - `app/gitea.py` — Async Gitea REST API client (repos, files, branches, PRs, token bootstrap)
  - `app/query_engine.py` — Query execution engine with three worker modes (see below)
  - `app/worker_manager.py` — Hetzner VM provisioning (remote mode only)
  - `app/shell.py` — Shell container user provisioning (generate username, create Linux account via SSH)
  - `app/workspace.py` — Local git working copy + filesystem operations (clone, list, read, write, rename, delete, git status)
  - `app/seed.py` — Seeds countries, server type rates, and catalog objects on first run
  - `app/routers/` — API route modules: `billing.py`, `catalog.py`, `projects.py`, `queries.py`, `settings.py`, `terminal.py`, `workers.py`
  - `alembic/` — Migration scripts; `env.py` reads `DATABASE_URL` (uses `psycopg2` sync driver for migrations)
- **`worker/`** — Standalone DuckDB query worker service (FastAPI)
  - `app.py` — HTTP endpoints: `POST /query`, `GET /query/{id}`, `POST /query/{id}/cancel`
  - `executor.py` — DuckDB execution: registers Iceberg tables via S3, writes results to parquet
  - `config.py` — Worker config, loaded from project root `.env` file
- **`shell/`** — Shared SSH container for user terminals
  - `Dockerfile` — Python 3.12-slim with sshd, git, dbt-core, sqlfluff; creates `shelluser` admin account
  - `entrypoint.sh` — Ensures `shelluser` home dir exists on volume mount, starts sshd
  - `keys/` — SSH keypair used by the backend to connect to the shell container
- **`frontend/`** — React 19 + TypeScript + Vite 7
  - `src/pages/ProjectEditor.tsx` — Project editor: file tree (react-complex-tree), Monaco editor with tabs, terminal panel
  - `src/pages/Engineering.tsx` — Project list and creation
  - `src/pages/QueryEditor.tsx` — SQL query editor
  - `src/components/Terminal.tsx` — xterm.js terminal with WebSocket bridge to shell container
  - `src/components/CatalogPanel.tsx` — Database/schema/table browser

## Shell Container (Multi-User Terminals)

A single shell container runs `sshd` and provides per-user terminal sessions. Each user gets their own Linux account and home directory.

### How It Works

1. **Shared `/home` mount**: The container's `/home` is bind-mounted from `./backend/data/homes` on the host. The backend and shell container see the same filesystem.
2. **Admin account**: `shelluser` is created at image build time with limited `sudo` rights (useradd, mkdir, cp, chown, id). The backend's SSH public key is mounted as its `authorized_keys`.
3. **User provisioning** (`app/shell.py`): On first terminal open, `ensure_shell_user()` derives a Linux username from the user's email, creates the account via SSH commands run as `shelluser`, and saves the `shell_username` on the `User` model. It creates `~/.ssh`, `~/.dbt`, and `~/projects` directories, copies skel files, and copies the backend's SSH key so it can later connect as that user.
4. **Terminal sessions** (`app/routers/terminal.py`): The WebSocket endpoint authenticates via JWT cookie, then SSHes into the shell container **as the user's own account** with `term_type="xterm-256color"`. It auto-cds into `~/projects/{repo_name}`.
5. **File operations** (`app/workspace.py`): The backend reads/writes project files directly on the host at `HOMES_PATH/{shell_username}/projects/{repo_name}` — the same files the shell user sees via the bind mount.

### Key Design Points

- Per-user home directories enable user-specific config files (`~/.dbt/profiles.yml`, etc.)
- Users are isolated at the Linux user level with standard file permissions
- The `/home` mount is shared, so users can see other users' home directories (read access depends on permissions)
- `shell_username` is persisted in PostgreSQL with a unique constraint; collisions append `-{user_id}`

### Configuration

Env vars in `.env`:
- `SHELL_SSH_HOST` — Shell container hostname (default: `localhost`)
- `SHELL_SSH_PORT` — SSH port (default: `2222`)
- `SHELL_SSH_USER` — Admin account for provisioning (default: `shelluser`)
- `SHELL_SSH_KEY_PATH` — Path to SSH private key (default: `shell/keys/id_ed25519`)
- `HOMES_PATH` — Host path to the shared `/home` mount (default: `./data/homes`)

## Projects and Project Editor

Projects are dbt projects backed by Gitea git repositories. Each project maps to a Gitea repo under the admin user.

### Project Lifecycle

1. **Create**: `POST /api/projects` creates a Gitea repo, commits a dbt scaffold (`dbt_project.yml`, `profiles.yml`), clones it into the user's home directory, and creates the standard dbt directories (`macros`, `models`, `seeds`, `tests`).
2. **Edit**: The project editor (`ProjectEditor.tsx`) provides a file tree, Monaco code editor with tabs, and a terminal panel.
3. **Delete**: `DELETE /api/projects/{id}` deletes the Gitea repo, removes the local clone, and deletes the DB row.

### File Tree

Uses **react-complex-tree** (`UncontrolledTreeEnvironment` + custom `FileTreeDataProvider`). Directories are lazy-loaded via `GET /api/projects/{id}/files?path=...`. Supports context menu operations: new file, new folder, rename, delete.

### Editor Tabs

Each open file gets a tab. Tab model: `OpenTab { path, content, savedContent }` — dirty detection via `content !== savedContent`. Monaco's `path` prop gives per-file undo history. Ctrl/Cmd+S saves via `POST /api/projects/{id}/files`.

### Terminal Panel

Collapsible panel at the bottom with multiple terminal tabs. Each tab creates a WebSocket to `ws://.../api/projects/{id}/terminal` which bridges to an SSH session in the shell container. Uses xterm.js with a dark theme. Supports resize events. State (open/closed, active tab, height) persisted to localStorage.

### File API Endpoints

All under `/api/projects/{project_id}/`:
- `GET /files?path=` — list directory
- `GET /file?path=` — read file content
- `POST /files` — create/write file
- `POST /folders` — create directory
- `POST /rename` — rename file/folder
- `DELETE /files` — delete file/folder
- `GET /status` — git status (porcelain)

### Configuration

- `GITEA_SHELL_URL` — Gitea URL as seen from the shell container (defaults to `GITEA_URL`). Used for git remote URLs so that terminals can push/pull.

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

## Local Development (docker-compose)

`docker-compose.yml` provides the local infrastructure. Run `docker compose up -d` to start all services:

- **`db`** — PostgreSQL 17 (port `5437`)
- **`minio`** / **`minio-init`** — S3-compatible object storage (ports `9000`/`9001`) + bucket creation
- **`gitea-init`** — Creates the `gitea` database in PostgreSQL
- **`gitea`** — Gitea 1.23 rootless (port `3000`), uses PostgreSQL for its own data
- **`gitea-setup`** — Creates the admin user (`kolkhis-admin`) via CLI after Gitea starts
- **`shell`** — SSH container for user terminals (port `2222`); `/home` bind-mounted from `./backend/data/homes`

Gitea env vars in `.env`: `GITEA_URL`, `GITEA_ADMIN_USER`, `GITEA_ADMIN_PASSWORD`, `GITEA_ADMIN_EMAIL`. On backend startup, `bootstrap_token()` creates a Gitea API token for the admin user.

## Database

PostgreSQL is required. Default connection: `euxine:very_secure_password@localhost:5437/euxine`. Override individual parts with `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB` env vars. The app auto-creates tables and seeds data on startup, so Alembic migrations are optional for dev.

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
