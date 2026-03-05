# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Kolkhis is a multi-tenant data platform with a Python/FastAPI backend and a React/TypeScript frontend, backed by PostgreSQL. It provides a SQL query interface and dbt project workspace over an Iceberg data lakehouse stored in S3-compatible object storage, with Lakekeeper as the REST catalog.

## Multi-Tenancy Model

Organizations are the top-level tenant boundary. New users go through an **onboarding flow** where they either create a new organization or request to join an existing one (pending admin approval).

- **Organization** — UUID primary key, unique name
- **OrgMembership** — Links users to orgs with `role` (admin/member), `status` (pending/active), and `shell_username`
- **JWT** contains one `org_id` at a time; users switch orgs via `/auth/switch-org`
- Each org gets: a Gitea organization, a `warehouse` git repo, an S3 bucket, and isolated shell home directories
- All workspace/terminal/catalog operations are scoped to the user's active org

## Architecture

- **`backend/`** — FastAPI app with async SQLAlchemy (asyncpg driver) and Alembic migrations
  - `app/main.py` — FastAPI app with lifespan handler (creates tables, seeds data, bootstraps Gitea token, manages workers on startup)
  - `app/models.py` — SQLAlchemy ORM models: Organization, User, OrgMembership, QueryJob, Country, UserSettings, WorkerVM, UsageEvent, ServerTypeRate, BillingPeriod
  - `app/database.py` — Async engine, session factory, and `get_db` FastAPI dependency
  - `app/config.py` — All configuration, loaded from project root `.env` file
  - `app/auth.py` — Google OAuth login, JWT token management (cookie + Bearer token), org switching
  - `app/billing.py` — Billing summary computation from usage events and server type rates
  - `app/warehouse.py` — PyIceberg `RestCatalog` (Lakekeeper) initialization, cached per org
  - `app/gitea.py` — Async Gitea REST API client (orgs, repos, files, branches, PRs, token bootstrap)
  - `app/query_engine.py` — Query execution engine with three worker modes (see below)
  - `app/worker_manager.py` — Hetzner VM provisioning (remote mode only)
  - `app/shell.py` — Shell user provisioning: auth file management, home directory setup, JWT generation for dbt CLI
  - `app/workspace.py` — Local git working copy + filesystem operations (clone, list, read, write, rename, delete, git status)
  - `app/seed.py` — Seeds countries and server type rates on first run
  - `app/routers/` — API route modules: `billing.py`, `catalog.py`, `dbt.py`, `orgs.py`, `queries.py`, `settings.py`, `terminal.py`, `workers.py`, `workspace.py`
  - `alembic/` — Migration scripts; `env.py` reads `DATABASE_URL` (uses `psycopg2` sync driver for migrations)
- **`worker/`** — Standalone DuckDB query worker service (FastAPI)
  - `app.py` — HTTP endpoints for job-based queries and persistent sessions (including Iceberg sessions)
  - `executor.py` — DuckDB execution: Iceberg catalog setup (ATTACH + namespace aliases), S3 config, query execution
  - `sessions.py` — Session lifecycle management (create, execute, keepalive, close, auto-reap idle sessions)
  - `config.py` — Worker config, loaded from project root `.env` file
- **`shell/`** — Shared SSH container for user terminals
  - `Dockerfile` — Python 3.12-slim with sshd, git, dbt-core, sqlfluff, dbt-kolkhis v0.6.0
  - `entrypoint.sh` — Merges system + user auth files, symlinks /etc/{passwd,shadow,group,gshadow} to /home/.auth/, starts sshd
  - `etc/` — Auth file templates (system user entries for passwd, shadow, group, gshadow)
  - `keys/` — SSH keypair used by the backend to connect to the shell container
- **`frontend/`** — React 19 + TypeScript + Vite 7
  - `src/App.tsx` — Main layout, sidebar navigation, auth guard, onboarding redirect, worker status indicator
  - `src/pages/Onboarding.tsx` — Organization create/join flow
  - `src/pages/ProjectEditor.tsx` — Workspace editor: file tree (react-complex-tree), Monaco editor with tabs, terminal panel
  - `src/pages/Engineering.tsx` — Wrapper for ProjectEditor
  - `src/pages/QueryEditor.tsx` — SQL query editor with multi-tab, catalog sidebar, results panel
  - `src/pages/Members.tsx` — Org member list with pending approval (admin)
  - `src/components/Terminal.tsx` — xterm.js terminal with WebSocket bridge to shell container
  - `src/components/CatalogPanel.tsx` — Database/schema/table browser tree
  - `src/components/CatalogDetails.tsx` — Database/schema/table detail panels

## Shell Container (Multi-User Terminals)

A single shell container runs `sshd` and provides per-user terminal sessions. Each user gets their own Linux account and home directory, scoped to their organization.

### How It Works

1. **Org-scoped `/home` mount**: The container's `/home` is bind-mounted from `./backend/data/homes/{org_id}` on the host. Each org gets its own mount.
2. **Auth file management** (`app/shell.py`): The backend writes Linux auth files (passwd, shadow, group, gshadow) directly to `/home/.auth/` with file locking. UIDs are allocated starting from 1000. No SSH commands needed — pure file I/O.
3. **User provisioning** (`ensure_shell_user()`): Derives a Linux username from email, writes auth entries, creates home directory with `~/.ssh`, `~/.dbt`, `~/projects`. Generates a long-lived JWT and writes `KOLKHIS_AUTH_TOKEN` + `KOLKHIS_BACKEND_URL` to `.bashrc` for dbt CLI.
4. **Terminal sessions** (`app/routers/terminal.py`): The WebSocket endpoint authenticates via JWT cookie, then SSHes into the shell container **as the user's own account**. Auto-cds into `~/projects/warehouse`.
5. **File operations** (`app/workspace.py`): The backend reads/writes files directly on the host at `HOMES_PATH/{org_id}/{shell_username}/projects/{repo_name}` — the same files the shell user sees via the bind mount.
6. **Container startup** (`entrypoint.sh`): Merges system auth templates with user entries from `/home/.auth/`, symlinks `/etc/{passwd,shadow,...}` → `/home/.auth/`, starts sshd.

### Key Design Points

- `shell_username` is stored on OrgMembership (not User), enabling different usernames per org
- Per-user home directories enable user-specific config (`~/.dbt/profiles.yml`, `.bashrc` with auth tokens)
- Path structure: `HOMES_PATH/{org_id}/{shell_username}/projects/warehouse/`
- `shell_username` has a global unique constraint; collisions append `-{user_id}`

### Configuration

Env vars in `.env`:
- `SHELL_SSH_HOST` — Shell container hostname (default: `localhost`)
- `SHELL_SSH_PORT` — SSH port (default: `2222`)
- `SHELL_SSH_KEY_PATH` — Path to SSH private key (default: `shell/keys/id_ed25519`)
- `SHELL_SSH_PUBKEY_PATH` — Path to SSH public key
- `SHELL_BACKEND_URL` — Backend URL as seen from shell container (for dbt profiles)
- `HOMES_PATH` — Host path to the shared `/home` mount (default: `./data/homes`)
- `SHELL_ORG_UUID` — Org UUID for docker-compose bind mount

## Organization Lifecycle

1. **Create** (`POST /api/orgs`): Creates Organization record + admin OrgMembership, provisions Gitea org + warehouse repo + S3 bucket, scaffolds dbt project (profiles.yml with `type: kolkhis` using env_var auth), provisions shell user in background.
2. **Join** (`POST /api/orgs/{org_id}/join`): Creates pending OrgMembership. Requires admin approval.
3. **Approve** (`POST /api/orgs/{org_id}/members/{user_id}/approve`): Admin sets status=active, provisions shell user + clones repo in background.
4. **Switch** (`POST /auth/switch-org`): Updates JWT cookie with new org_id/org_role.

## Workspace and Project Editor

Each org has a single `warehouse` git repo in Gitea. All users in the org share the same repo (cloned per-user into `~/projects/warehouse`). The project editor provides a file tree, Monaco code editor, and terminal panel.

### File API Endpoints

All under `/api/workspace/`:
- `GET /files?path=` — list directory
- `GET /file?path=` — read file content
- `POST /files` — create/write file
- `POST /folders` — create directory
- `POST /rename` — rename file/folder
- `DELETE /files` — delete file/folder
- `GET /status` — git status (porcelain)

### Configuration

- `GITEA_SHELL_URL` — Gitea URL as seen from the shell container (defaults to `GITEA_URL`). Used for git remote URLs so that terminals can push/pull.

## Iceberg Catalog (Lakekeeper)

Lakekeeper is the Iceberg REST catalog. It stores table metadata in PostgreSQL and data in S3.

- Backend uses PyIceberg `RestCatalog` (`app/warehouse.py`), cached per org via `get_org_catalog(org_id)`
- Worker uses DuckDB's native `ATTACH ... TYPE ICEBERG` for DDL/DML support
- Namespace aliases: nested Iceberg namespaces like `retail.products` are aliased as in-memory DuckDB databases/schemas so that `retail.products.table_name` works as `database.schema.table` in both the SQL Query workbook and dbt models
- Config: `LAKEKEEPER_URL` (default: `http://localhost:8181`)

## dbt Integration

The dbt-kolkhis adapter (`dbt-kolkhis` v0.6.0) connects dbt CLI to Kolkhis via the backend's session proxy.

### How It Works

1. Shell container has `dbt-core` + `dbt-kolkhis` installed
2. Each user's `.bashrc` exports `KOLKHIS_AUTH_TOKEN` and `KOLKHIS_BACKEND_URL`
3. Warehouse `profiles.yml` uses `type: kolkhis` with `env_var()` for auth
4. dbt CLI → backend `/api/dbt/session` proxy → worker Iceberg session
5. Worker creates a persistent DuckDB connection with Lakekeeper ATTACH + namespace aliases

### dbt Session Endpoints

- `POST /api/dbt/session` — Create or reuse Iceberg session on worker
- `POST /api/dbt/session/{session_id}/query` — Execute SQL in session
- `DELETE /api/dbt/session/{session_id}` — Close session

## Worker Modes

Controlled by `WORKER_MODE` in `.env`:

- **`local`** — DuckDB runs in-process within the backend. Simplest setup, no separate worker needed.
- **`local-worker`** — Backend sends queries over HTTP to a locally-running worker app. Same protocol as remote, but no VM provisioning.
- **`remote`** — Backend provisions Hetzner VMs, sends queries over HTTP to the worker running on the VM. Results are written to S3.

### Worker Endpoints

Job-based: `POST /query`, `GET /query/{id}`, `POST /query/{id}/cancel`
Session-based: `POST /session`, `POST /session/iceberg`, `POST /session/{id}/query`, `DELETE /session/{id}`, `POST /session/{id}/keepalive`

### Local Worker Setup

1. Set in `.env`:
   ```
   WORKER_MODE=local-worker
   WORKER_URL=http://localhost:8080
   WORKER_AUTH_TOKEN=<any shared token>
   ```
2. Start the worker: `cd worker && uvicorn app:app --port 8080`
3. Start the backend: `cd backend && uvicorn app.main:app --reload`

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

- **`db`** — PostgreSQL 17 (port `5437`); hosts databases: `euxine`, `gitea`, `lakekeeper`, `dagster`
- **`minio`** / **`minio-init`** — S3-compatible object storage (ports `9000`/`9001`) + bucket creation
- **`gitea-init`** — Creates the `gitea` database in PostgreSQL
- **`gitea`** — Gitea 1.23 rootless (port `3000`), uses PostgreSQL for its own data
- **`gitea-setup`** — Creates the admin user (`kolkhis-admin`) via CLI after Gitea starts
- **`lakekeeper-init`** / **`lakekeeper-migrate`** / **`lakekeeper`** — Iceberg REST catalog (port `8181`)
- **`lakekeeper-setup`** — Creates `warehouse` S3 storage profile on Lakekeeper
- **`shell`** — SSH container for user terminals (port `2222`); `/home` bind-mounted from `./backend/data/homes/{SHELL_ORG_UUID}`
- **`dagster-init`** / **`dagster-code`** / **`dagster-webserver`** / **`dagster-daemon`** — Dagster orchestration (port `3030`)

## Database

PostgreSQL is required. Default connection: `euxine:very_secure_password@localhost:5437/euxine`. Override with `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`. The app auto-creates tables and seeds data on startup.

Shared PostgreSQL instance hosts: `euxine` (app), `gitea`, `lakekeeper`, `dagster`.

## Object Storage

The Iceberg warehouse lives in S3-compatible storage. Configured via `WAREHOUSE_PATH`, `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_REGION` in `.env`.

- **Local development**: MinIO at `localhost:9000`, bucket `warehouse`
- **Production**: Hetzner Object Storage

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
