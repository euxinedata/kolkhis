# Dagster Code Deployment — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable automatic deployment of Dagster definitions from org warehouse repos to the dagster-code service, triggered by CI after successful lint.

**Architecture:** Custom dagster-code entrypoint with HTTP reload API + gRPC lifecycle management. Backend orchestrates code loading. Gitea Actions CI triggers reload after lint passes.

**Tech Stack:** Python (http.server, subprocess), Dagster gRPC, Git, Docker, FastAPI (backend)

---

### Task 1: Create the dagster-code entrypoint

**Files:**
- Create: `docker/dagster/entrypoint.py`

**Context:** This Python script runs as the main process in the dagster-code container. It serves two purposes: (1) an HTTP API on port 3031 for reload/status, and (2) managing the dagster gRPC subprocess on port 3030. It uses only stdlib — no pip dependencies beyond what's already in the Dagster image.

**Step 1: Write entrypoint.py**

```python
"""
dagster-code entrypoint: HTTP reload API + Dagster gRPC lifecycle.

Endpoints:
  POST /reload  {"org_id": "...", "repo": "warehouse"}
    → git clone/pull from Gitea, (re)start gRPC if dagster/definitions.py exists
  GET  /status
    → {"loaded": bool, "org_id": str|null, "commit": str|null, "grpc_running": bool}
"""

import json
import logging
import os
import signal
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("entrypoint")

REPOS_DIR = Path("/opt/dagster/repos")
GITEA_URL = os.environ.get("GITEA_SHELL_URL", "http://gitea:3000")
GITEA_USER = os.environ.get("GITEA_ADMIN_USER", "")
GITEA_PASS = os.environ.get("GITEA_ADMIN_PASSWORD", "")

# State
_lock = threading.Lock()
_grpc_proc: subprocess.Popen | None = None
_current_org: str | None = None
_current_commit: str | None = None


def _git_clone_or_pull(org_id: str, repo: str) -> Path:
    """Clone or pull the repo. Returns the repo path."""
    repo_dir = REPOS_DIR / org_id / repo
    # Build authenticated URL
    auth_url = GITEA_URL.replace("://", f"://{GITEA_USER}:{GITEA_PASS}@")
    remote = f"{auth_url}/{org_id}/{repo}.git"

    if (repo_dir / ".git").exists():
        log.info("Pulling %s/%s", org_id, repo)
        subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=repo_dir, capture_output=True, check=True,
        )
    else:
        log.info("Cloning %s/%s", org_id, repo)
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", remote, str(repo_dir)],
            capture_output=True, check=True,
        )
    return repo_dir


def _get_commit(repo_dir: Path) -> str | None:
    """Return the current HEAD short SHA."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_dir, capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except Exception:
        return None


def _stop_grpc() -> None:
    """Stop the running gRPC process if any."""
    global _grpc_proc
    if _grpc_proc and _grpc_proc.poll() is None:
        log.info("Stopping gRPC server (pid %d)", _grpc_proc.pid)
        _grpc_proc.terminate()
        try:
            _grpc_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _grpc_proc.kill()
            _grpc_proc.wait()
    _grpc_proc = None


def _start_grpc(repo_dir: Path) -> bool:
    """Start the gRPC server if definitions.py exists. Returns True if started."""
    global _grpc_proc
    definitions_file = repo_dir / "dagster" / "definitions.py"
    if not definitions_file.exists():
        log.info("No dagster/definitions.py found — gRPC not started")
        return False

    _stop_grpc()
    dagster_dir = repo_dir / "dagster"
    log.info("Starting gRPC server from %s", dagster_dir)
    _grpc_proc = subprocess.Popen(
        [
            "dagster", "api", "grpc",
            "-h", "0.0.0.0",
            "-p", "3030",
            "-f", str(definitions_file),
        ],
        cwd=str(dagster_dir),
    )
    log.info("gRPC server started (pid %d)", _grpc_proc.pid)
    return True


def _do_reload(org_id: str, repo: str) -> dict:
    """Clone/pull and (re)start gRPC. Returns status dict."""
    global _current_org, _current_commit
    with _lock:
        repo_dir = _git_clone_or_pull(org_id, repo)
        _current_org = org_id
        _current_commit = _get_commit(repo_dir)
        loaded = _start_grpc(repo_dir)
        return {
            "loaded": loaded,
            "org_id": org_id,
            "commit": _current_commit,
            "grpc_running": loaded,
        }


def _get_status() -> dict:
    """Return current state."""
    with _lock:
        grpc_running = _grpc_proc is not None and _grpc_proc.poll() is None
        return {
            "loaded": grpc_running,
            "org_id": _current_org,
            "commit": _current_commit,
            "grpc_running": grpc_running,
        }


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/reload":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else {}
                org_id = body.get("org_id")
                repo = body.get("repo", "warehouse")
                if not org_id:
                    self._json(400, {"error": "org_id required"})
                    return
                result = _do_reload(org_id, repo)
                self._json(200, result)
            except subprocess.CalledProcessError as e:
                log.error("Git operation failed: %s", e.stderr)
                self._json(500, {"error": "git operation failed", "detail": str(e.stderr)})
            except Exception as e:
                log.exception("Reload failed")
                self._json(500, {"error": str(e)})
        else:
            self._json(404, {"error": "not found"})

    def do_GET(self):
        if self.path == "/status":
            self._json(200, _get_status())
        else:
            self._json(404, {"error": "not found"})

    def _json(self, code: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        log.info(fmt, *args)


def main():
    REPOS_DIR.mkdir(parents=True, exist_ok=True)

    # Graceful shutdown
    def _shutdown(signum, frame):
        log.info("Shutting down (signal %d)", signum)
        _stop_grpc()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    server = HTTPServer(("0.0.0.0", 3031), Handler)
    log.info("Reload API listening on :3031")
    server.serve_forever()


if __name__ == "__main__":
    main()
```

**Step 2: Verify the script is syntactically valid**

Run: `cd /Users/rseed42/Projects/euxine/kolkhis && python3 -c "import ast; ast.parse(open('docker/dagster/entrypoint.py').read()); print('OK')"`

Expected: `OK`

**Step 3: Commit**

```bash
git add docker/dagster/entrypoint.py
git commit -m "Add dagster-code entrypoint with reload API and gRPC lifecycle"
```

---

### Task 2: Update Dockerfile

**Files:**
- Modify: `docker/dagster/Dockerfile`

**Context:** The existing Dockerfile is a minimal Python 3.12-slim image with dagster packages. We need to: (1) add `git` for cloning repos, (2) copy the entrypoint script, (3) set it as the default command. The Dockerfile must NOT break the dagster-webserver and dagster-daemon services which also use this image — those services override `command` in docker-compose.

**Step 1: Update the Dockerfile**

The full updated Dockerfile:

```dockerfile
FROM python:3.12-slim

RUN apt-get update -qq && apt-get install -y -qq git > /dev/null 2>&1 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    dagster \
    dagster-webserver \
    dagster-postgres \
    dagster-k8s \
    dagster-dbt \
    dbt-core

COPY dagster.yaml workspace.yaml /opt/dagster/
COPY entrypoint.py /opt/dagster/entrypoint.py

ENV DAGSTER_HOME=/opt/dagster

WORKDIR /opt/dagster

CMD ["python", "entrypoint.py"]
```

Key changes from original:
- Added `git` installation (line 3)
- Copied `entrypoint.py` (line 12)
- Added `CMD` to run entrypoint by default (line 18) — dagster-webserver and dagster-daemon override this via `command` in docker-compose

**Step 2: Verify the Dockerfile builds**

Run: `cd /Users/rseed42/Projects/euxine/kolkhis && docker build -t dagster-code-test ./docker/dagster`

Expected: Build succeeds

**Step 3: Commit**

```bash
git add docker/dagster/Dockerfile
git commit -m "Update Dagster Dockerfile: add git, entrypoint, CMD"
```

---

### Task 3: Update docker-compose.yml

**Files:**
- Modify: `docker-compose.yml:114-123`

**Context:** The dagster-code service currently runs `dagster api grpc` directly via `command`. We need to: (1) remove the `command` override so the Dockerfile CMD (entrypoint.py) runs, (2) add Gitea env vars for git auth, (3) expose port 3031 for the reload API. The dagster-webserver and dagster-daemon services are unchanged — they still override `command`.

**Step 1: Update the dagster-code service**

Replace lines 114-123 of docker-compose.yml (the dagster-code service block) with:

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

Changes from original:
- Removed `command: ["dagster", "api", "grpc", ...]` — entrypoint.py handles gRPC startup
- Added `GITEA_SHELL_URL`, `GITEA_ADMIN_USER`, `GITEA_ADMIN_PASSWORD` env vars
- Added port `3031:3031` for reload API

**Step 2: Verify docker-compose config is valid**

Run: `cd /Users/rseed42/Projects/euxine/kolkhis && docker compose config --services`

Expected: All services listed without errors

**Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "Update dagster-code service: custom entrypoint, Gitea env vars, reload port"
```

---

### Task 4: Add DAGSTER_CODE_URL to backend config

**Files:**
- Modify: `backend/app/config.py:79-83` (after the Gitea config block)

**Context:** The backend needs to know the dagster-code reload API URL. Follow the existing pattern for config vars: `os.environ.get()` with a sensible default.

**Step 1: Add the config var**

After line 83 (`HOMES_PATH = ...`), add:

```python
# Dagster code deployment
DAGSTER_CODE_URL = os.environ.get("DAGSTER_CODE_URL", "http://dagster-code:3031")
```

**Step 2: Add to .env**

Add to the end of `.env` (before any comments about shell container):

```
# Dagster code deployment
DAGSTER_CODE_URL=http://dagster-code:3031
```

**Step 3: Commit**

```bash
git add backend/app/config.py
git commit -m "Add DAGSTER_CODE_URL config for dagster-code reload API"
```

Note: `.env` is gitignored, so it won't be committed.

---

### Task 5: Update orgs.py — reload dagster-code on org creation

**Files:**
- Modify: `backend/app/routers/orgs.py:1-12` (imports)
- Modify: `backend/app/routers/orgs.py:176-192` (create_org provisioning block)

**Context:** After the warehouse repo scaffold is created in Gitea, the backend calls dagster-code's `/reload` endpoint to prime the git clone. This is a fire-and-forget call — if dagster-code is down, org creation still succeeds (the reload will happen on the next CI push). Add `httpx` import (already a dependency — used in `ci.py`) and the `DAGSTER_CODE_URL` config import.

**Step 1: Update imports**

Add `httpx` to imports and add `DAGSTER_CODE_URL` to the config import line.

Current line 11:
```python
from app.config import S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY, S3_REGION, S3_BUCKET_NAME, SHELL_MODE
```

Updated:
```python
from app.config import S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY, S3_REGION, S3_BUCKET_NAME, SHELL_MODE, DAGSTER_CODE_URL
```

Add after existing imports (line 17):
```python
import httpx
```

**Step 2: Add dagster reload call after scaffold creation**

In `create_org()`, after line 189 (`await _create_org_storage(org.id, db)`) and before the `except` block, add:

```python
        # Prime dagster-code with the new org's repo
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{DAGSTER_CODE_URL}/reload",
                    json={"org_id": org.id, "repo": WAREHOUSE_REPO},
                )
        except Exception:
            logger.warning("dagster-code reload failed for org %s (non-fatal)", org.id)
```

This is inside the existing `try` block but has its own inner `try/except` so dagster-code failures don't block org creation.

**Step 3: Commit**

```bash
git add backend/app/routers/orgs.py
git commit -m "Call dagster-code /reload on org creation"
```

---

### Task 6: Update CI workflow scaffold with deploy step

**Files:**
- Modify: `backend/app/routers/orgs.py:107-119` (the CI workflow in _WAREHOUSE_SCAFFOLD)

**Context:** The `.gitea/workflows/ci.yml` scaffold needs a final step that triggers dagster-code reload after successful lint. The org ID is extracted from `GITHUB_REPOSITORY` (format: `{org_uuid}/warehouse`). The `curl` call runs inside the CI container, which is on the `kolkhis_default` Docker network and can reach `dagster-code:3031`.

**Step 1: Update the CI workflow scaffold**

Replace the CI workflow entry in `_WAREHOUSE_SCAFFOLD`:

```python
    ".gitea/workflows/ci.yml": """\
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
  deploy:
    runs-on: ubuntu-latest
    needs: lint
    steps:
      - run: |
          REPO_OWNER=$(echo $GITHUB_REPOSITORY | cut -d/ -f1)
          curl -sf -X POST http://dagster-code:3031/reload \
            -H 'Content-Type: application/json' \
            -d "{\\"org_id\\": \\"$REPO_OWNER\\", \\"repo\\": \\"warehouse\\"}"
""",
```

Key points:
- `deploy` is a separate job that `needs: lint` — only runs if lint passes
- Uses `ubuntu-latest` (same runner image) — `curl` is available in `node:20-bookworm`
- Extracts org ID from `GITHUB_REPOSITORY` env var
- `curl -sf` fails silently on HTTP errors (returns non-zero exit code)

**Step 2: Commit**

```bash
git add backend/app/routers/orgs.py
git commit -m "Add dagster deploy step to CI workflow scaffold"
```

---

### Task 7: Integration test — rebuild and verify

**Context:** This task verifies the full flow works end-to-end in the local docker-compose environment.

**Step 1: Rebuild the dagster-code image**

Run: `cd /Users/rseed42/Projects/euxine/kolkhis && docker compose build dagster-code`

Expected: Build succeeds

**Step 2: Restart dagster services**

Run: `docker compose up -d dagster-code dagster-webserver dagster-daemon`

Expected: All three containers start. `dagster-code` logs show "Reload API listening on :3031"

**Step 3: Verify the reload API responds**

Run: `curl -s http://localhost:3031/status`

Expected: `{"loaded": false, "org_id": null, "commit": null, "grpc_running": false}`

**Step 4: Test reload with the existing org**

Run (replace org UUID with actual value from .env):
```bash
curl -s -X POST http://localhost:3031/reload \
  -H "Content-Type: application/json" \
  -d '{"org_id": "22ec7b00-ed8f-4836-b7fc-8d01fdd82fa1", "repo": "warehouse"}'
```

Expected: `{"loaded": false, "org_id": "22ec7b00-...", "commit": "...", "grpc_running": false}` (loaded=false because dagster/ has no definitions.py yet)

**Step 5: Verify the clone happened**

Run: `docker compose exec dagster-code ls /opt/dagster/repos/22ec7b00-ed8f-4836-b7fc-8d01fdd82fa1/warehouse/dagster/`

Expected: `.gitkeep` (the scaffold placeholder)

**Step 6: Commit all remaining changes**

```bash
git add -A
git commit -m "Phase 2: Dagster code deployment infrastructure"
```
