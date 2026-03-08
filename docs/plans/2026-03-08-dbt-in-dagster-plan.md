# dbt-in-Dagster Scheduling Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable scheduled dbt model runs inside dagster-code, using service JWTs and dagster-dbt's `DbtCliResource`.

**Architecture:** Backend mints a service JWT per org, passes it to dagster-code via /reload. dagster-code sets env vars for the gRPC subprocess. Dagster definitions use `DbtCliResource` which shells out to `dbt run` with the kolkhis adapter.

**Tech Stack:** FastAPI, PyJWT, dagster-dbt, dbt-kolkhis, Docker

---

### Task 1: Add `make_service_token` to backend auth

**Files:**
- Modify: `backend/app/auth.py:1-10` (imports) and after line 44 (after `_make_token`)

**Step 1: Add the function**

After the existing `_make_token` function (line 44), add:

```python
def make_service_token(org_id: str) -> str:
    """Mint a long-lived service JWT for dagster (no expiry)."""
    return jwt.encode(
        {"sub": "service:dagster", "org_id": org_id, "name": "dagster"},
        JWT_SECRET,
        algorithm="HS256",
    )
```

**Step 2: Commit**

```bash
git add backend/app/auth.py
git commit -m "Add make_service_token for dagster service JWT"
```

---

### Task 2: Handle service token in dbt router

The dbt router uses `int(user["sub"])` in multiple places (lines 70, 179, 255), which will crash for `"service:dagster"`. It also keys `_active_sessions` by int user_id.

**Files:**
- Modify: `backend/app/routers/dbt.py:31-32` (session dict type), lines 70, 179, 255 (int casts), line 189 (QueryJob user_id)

**Step 1: Change `_active_sessions` key type to `str`**

Line 32: change `dict[int, dict]` to `dict[str, dict]`:

```python
_active_sessions: dict[str, dict] = {}
```

**Step 2: Replace `int(user["sub"])` with `user["sub"]` (string)**

At line 70 (`create_session`):
```python
    user_id = user["sub"]  # str — numeric for users, "service:dagster" for service tokens
```

At line 179 (`session_query`):
```python
    user_id = user["sub"]
```

At line 255 (`close_session`):
```python
    user_id = user["sub"]
```

**Step 3: Make QueryJob.user_id nullable for service tokens**

At line 189, change the QueryJob creation:
```python
    # Service tokens have non-numeric sub — skip user_id
    numeric_user_id = int(user_id) if user_id.isdigit() else None
    job = QueryJob(
        id=job_id, user_id=numeric_user_id, sql=body.sql,
        status="running", started_at=now,
    )
```

**Step 4: Fix `_get_worker_url` call**

At line 102, `_get_worker_url(user_id)` passes to `ensure_worker(user_id)` in remote mode which expects an int. For service tokens, always use `WORKER_URL` (dagster runs locally):

```python
    worker_url = await _get_worker_url(user_id) if user_id.isdigit() else WORKER_URL
```

**Step 5: Fix `_touch_worker_vm` call**

At line 197, same issue — skip for service tokens:

```python
    if user_id.isdigit():
        await _touch_worker_vm(int(user_id))
```

**Step 6: Commit**

```bash
git add backend/app/routers/dbt.py
git commit -m "Handle service token sub in dbt router"
```

---

### Task 3: Include auth_token and backend_url in /reload payload

**Files:**
- Modify: `backend/app/routers/orgs.py:11-15` (imports), lines 205-216 (dagster reload block)

**Step 1: Import `make_service_token` and `SHELL_BACKEND_URL`**

Update the imports at line 12-15:

```python
from app.auth import make_service_token, require_auth
from app.config import (
    S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY, S3_REGION, S3_BUCKET_NAME,
    SHELL_MODE, DAGSTER_CODE_URL, DAGSTER_RELOAD_TOKEN, SHELL_BACKEND_URL,
)
```

**Step 2: Add auth_token and backend_url to the reload payload**

In the dagster reload block (around line 209-213), update the JSON payload:

```python
                await client.post(
                    f"{DAGSTER_CODE_URL}/reload",
                    json={
                        "org_id": org.id,
                        "repo": WAREHOUSE_REPO,
                        "auth_token": make_service_token(org.id),
                        "backend_url": SHELL_BACKEND_URL,
                    },
                    headers=headers,
                )
```

**Step 3: Commit**

```bash
git add backend/app/routers/orgs.py
git commit -m "Include service token and backend URL in dagster reload payload"
```

---

### Task 4: Update entrypoint.py to pass auth env vars to gRPC subprocess

**Files:**
- Modify: `docker/dagster/entrypoint.py:29-33` (state vars), `_do_reload` (lines 105-117), `_start_grpc` (lines 82-102)

**Step 1: Add state variables for auth**

After line 33, add:

```python
_current_auth_token: str | None = None
_current_backend_url: str | None = None
```

**Step 2: Extract auth from reload payload**

In `_do_reload` (line 105), after `org_id = body.get("org_id")`, store the auth fields:

```python
def _do_reload(org_id: str, repo: str, auth_token: str | None = None, backend_url: str | None = None) -> dict:
    global _current_org, _current_commit, _current_auth_token, _current_backend_url
    with _lock:
        repo_dir = _git_clone_or_pull(org_id, repo)
        _current_org = org_id
        _current_commit = _get_commit(repo_dir)
        _current_auth_token = auth_token
        _current_backend_url = backend_url
        loaded = _start_grpc(repo_dir)
        return {
            "loaded": loaded,
            "org_id": org_id,
            "commit": _current_commit,
            "grpc_running": loaded,
        }
```

**Step 3: Pass env vars to gRPC subprocess**

In `_start_grpc` (line 92), add env to the Popen call:

```python
def _start_grpc(repo_dir: Path) -> bool:
    global _grpc_proc
    definitions_file = repo_dir / "dagster" / "definitions.py"
    if not definitions_file.exists():
        log.info("No dagster/definitions.py found -- gRPC not started")
        return False

    _stop_grpc()
    dagster_dir = repo_dir / "dagster"
    log.info("Starting gRPC server from %s", dagster_dir)

    env = os.environ.copy()
    if _current_auth_token:
        env["KOLKHIS_AUTH_TOKEN"] = _current_auth_token
    if _current_backend_url:
        env["KOLKHIS_BACKEND_URL"] = _current_backend_url
    env["DBT_USER"] = "dagster"

    _grpc_proc = subprocess.Popen(
        [
            "dagster", "api", "grpc",
            "-h", "0.0.0.0",
            "-p", "3030",
            "-f", str(definitions_file),
        ],
        cwd=str(dagster_dir),
        env=env,
    )
    log.info("gRPC server started (pid %d)", _grpc_proc.pid)
    return True
```

**Step 4: Update the POST /reload handler to pass auth fields**

In the Handler `do_POST` method (around line 154):

```python
                result = _do_reload(
                    org_id, repo,
                    auth_token=body.get("auth_token"),
                    backend_url=body.get("backend_url"),
                )
```

**Step 5: Commit**

```bash
git add docker/dagster/entrypoint.py
git commit -m "Pass auth env vars to dagster gRPC subprocess"
```

---

### Task 5: Install dbt-kolkhis in dagster Dockerfile

**Files:**
- Modify: `docker/dagster/Dockerfile:5-11` (pip install line)

**Step 1: Add dbt-kolkhis to pip install**

```dockerfile
RUN pip install --no-cache-dir \
    dagster \
    dagster-webserver \
    dagster-postgres \
    dagster-k8s \
    dagster-dbt \
    dbt-core \
    "dbt-kolkhis @ git+https://github.com/euxinedata/dbt-kolkhis.git@v1.0.0"
```

**Step 2: Commit**

```bash
git add docker/dagster/Dockerfile
git commit -m "Install dbt-kolkhis in dagster container"
```

---

### Task 6: Pass SHELL_BACKEND_URL to dagster-code in docker-compose

**Files:**
- Modify: `docker-compose.yml:114-129` (dagster-code service)

**Step 1: Add SHELL_BACKEND_URL to dagster-code environment**

```yaml
  dagster-code:
    build: ./docker/dagster
    environment:
      - DAGSTER_PG_URL=postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/dagster
      - DAGSTER_HOME=/opt/dagster
      - GITEA_SHELL_URL=${GITEA_SHELL_URL:-http://gitea:3000}
      - GITEA_ADMIN_USER=${GITEA_ADMIN_USER:-kolkhis-admin}
      - GITEA_ADMIN_PASSWORD=${GITEA_ADMIN_PASSWORD:-kolkhis-dev-2026}
      - DAGSTER_RELOAD_TOKEN=${DAGSTER_RELOAD_TOKEN:-dagster-reload-dev}
      - SHELL_BACKEND_URL=${SHELL_BACKEND_URL:-http://host.docker.internal:8000}
```

**Step 2: Commit**

```bash
git add docker-compose.yml
git commit -m "Pass SHELL_BACKEND_URL to dagster-code service"
```

---

### Task 7: End-to-end test

**No code changes — manual verification.**

**Step 1: Rebuild dagster-code**

```bash
docker compose build dagster-code
docker compose up -d dagster-code dagster-webserver dagster-daemon
```

**Step 2: Trigger reload with auth token**

```bash
# From the backend, create org or manually call reload
curl -X POST http://localhost:3031/reload \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer dagster-reload-dev' \
  -d '{"org_id": "<ORG_UUID>", "repo": "warehouse", "auth_token": "<SERVICE_JWT>", "backend_url": "http://host.docker.internal:8000"}'
```

**Step 3: Push a definitions.py to the warehouse repo**

Create `dagster/definitions.py` in the org's warehouse repo via Gitea API or the UI with:

```python
from dagster import Definitions, AssetExecutionContext
from dagster_dbt import DbtCliResource, dbt_assets, DbtProject

project = DbtProject(project_dir="..")

@dbt_assets(manifest=project.manifest_path)
def warehouse_dbt_assets(context: AssetExecutionContext, dbt: DbtCliResource):
    yield from dbt.cli(["run"], context=context).stream()

defs = Definitions(
    assets=[warehouse_dbt_assets],
    resources={"dbt": DbtCliResource(project_dir="..")},
)
```

**Step 4: Verify in Dagster UI**

Open `http://localhost:3030` — should show dbt assets from the warehouse project.

**Step 5: Trigger a materialization**

Click "Materialize" on an asset in the Dagster UI. Check that dbt runs successfully, connecting to the backend via the service token.
