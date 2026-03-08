# Phase 2: Dagster Code Deployment — Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable engineers to write Dagster Python definitions in the warehouse repo's `dagster/` directory and have them automatically deployed to the dagster-code service on push (after CI passes).

**Architecture:** The dagster-code container runs a custom entrypoint that exposes a small HTTP reload API alongside the Dagster gRPC server. The backend orchestrates code loading — telling dagster-code which org's repo to clone from Gitea. The existing Gitea Actions CI pipeline triggers a reload after successful lint.

**Tech Stack:** Python, Dagster, Gitea (git), FastAPI (reload API), Docker

---

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| How engineers define code | Full Dagster Python (no dbt auto-discovery) | Engineers write custom definitions.py with assets, jobs, schedules |
| Multi-tenancy | Single org for now (local dev) | One dynamically-created org; multi-org code locations deferred to Phase 3 |
| Code delivery | Git clone from Gitea | Decoupled from filesystem layout, works same locally and in production |
| Reload trigger | CI-triggered (Gitea Actions) | Broken code (lint failure) never gets deployed; piggybacks on existing CI |
| Backend role | Orchestrates dagster-code | Backend tells dagster-code which org/repo to load (same provisioning pattern as Gitea, S3, shell) |
| Container approach | Custom entrypoint in existing Dagster image | Single container with git clone + reload API + gRPC server |
| Empty dagster/ handling | Don't start gRPC | No code location until engineer creates definitions.py; /status reports this |

## dagster-code Container

### Custom Entrypoint

A Python script (`entrypoint.py`) that runs inside the dagster-code container:

1. **HTTP server** on port 3031 with two endpoints:
   - `POST /reload` — accepts `{"org_id": "...", "repo": "warehouse"}`, clones or pulls from Gitea, (re)starts gRPC if `dagster/definitions.py` exists
   - `GET /status` — returns current state: `{"loaded": bool, "org_id": "...", "commit": "...", "grpc_running": bool}`

2. **Git operations:**
   - Clones `{GITEA_SHELL_URL}/{org_id}/{repo}.git` into `/opt/dagster/repos/{org_id}/{repo}`
   - Uses `GITEA_ADMIN_USER` / `GITEA_ADMIN_PASSWORD` for git auth
   - On subsequent reloads: `git pull` instead of clone

3. **gRPC lifecycle:**
   - After clone/pull, checks if `dagster/definitions.py` exists in the cloned repo
   - If yes: starts `dagster api grpc -h 0.0.0.0 -p 3030 -m dagster -f dagster/definitions.py` as a subprocess
   - If already running: kills the existing gRPC process, starts a new one
   - If no `definitions.py`: does not start gRPC, `/status` reports `loaded: false`

### Ports

- **3030**: Dagster gRPC (unchanged, only active when definitions exist)
- **3031**: Reload HTTP API (new)

### Environment Variables

Reuses existing vars — no new env vars except `DAGSTER_CODE_URL` on the backend side:

- `GITEA_SHELL_URL` — Internal Gitea URL (e.g., `http://gitea:3000`)
- `GITEA_ADMIN_USER` / `GITEA_ADMIN_PASSWORD` — Git auth
- `DAGSTER_PG_URL` — Dagster storage (unchanged)

## Backend Changes

### Org Creation

In `backend/app/routers/orgs.py`, after the Gitea repo scaffold step, the backend calls dagster-code's `/reload` endpoint:

```python
async with httpx.AsyncClient() as client:
    await client.post(f"{DAGSTER_CODE_URL}/reload", json={
        "org_id": org.id,
        "repo": "warehouse",
    })
```

This primes the git clone. Since `dagster/` starts empty (`.gitkeep` only), gRPC won't start — which is correct.

### Configuration

New env var in `.env`:

```
DAGSTER_CODE_URL=http://dagster-code:3031
```

Backend reads this in `app/config.py`.

## CI Workflow Change

The `.gitea/workflows/ci.yml` scaffold in `orgs.py` adds a deploy step after lint:

```yaml
name: CI
on: [push]
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: |
          apt-get update -qq && apt-get install -y -qq python3 python3-pip > /dev/null 2>&1
          pip install -q --break-system-packages sqlfluff
      - run: sqlfluff lint models/ --dialect duckdb
      - run: |
          REPO_OWNER=$(echo $GITHUB_REPOSITORY | cut -d/ -f1)
          curl -sf -X POST http://dagster-code:3031/reload \
            -H "Content-Type: application/json" \
            -d "{\"org_id\": \"$REPO_OWNER\", \"repo\": \"warehouse\"}"
```

The org ID is extracted from `GITHUB_REPOSITORY` (which is `{org_uuid}/warehouse` in Gitea).

## docker-compose Changes

```yaml
dagster-code:
  build: ./docker/dagster
  environment:
    - DAGSTER_PG_URL=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/dagster
    - DAGSTER_HOME=/opt/dagster
    - GITEA_SHELL_URL=${GITEA_SHELL_URL:-http://gitea:3000}
    - GITEA_ADMIN_USER=${GITEA_ADMIN_USER:-kolkhis-admin}
    - GITEA_ADMIN_PASSWORD=${GITEA_ADMIN_PASSWORD:-kolkhis-dev-2026}
  ports:
    - "3031:3031"
  volumes:
    - ./docker/dagster/dagster-local.yaml:/opt/dagster/dagster.yaml
  depends_on:
    - dagster-init
```

Note: the `command` override is removed — the custom entrypoint handles starting both the HTTP API and the gRPC server.

## File Changes Summary

| File | Action | Description |
|------|--------|-------------|
| `docker/dagster/Dockerfile` | Modify | Add git, copy entrypoint.py, set as CMD |
| `docker/dagster/entrypoint.py` | Create | HTTP reload API + gRPC lifecycle manager |
| `docker-compose.yml` | Modify | Update dagster-code service (env vars, ports, remove command) |
| `backend/app/config.py` | Modify | Add DAGSTER_CODE_URL |
| `backend/app/routers/orgs.py` | Modify | Call dagster-code /reload after org creation; update CI workflow scaffold |
| `.env` | Modify | Add DAGSTER_CODE_URL |

## Not In Scope

- Multi-org code locations (Phase 3)
- Dagster resource configuration (database connections, S3 per org)
- Production K8s changes
- dbt auto-discovery / dagster-dbt integration
- Dagster UI access control
