# Gitea Actions CI — Phase 1 Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add CI to the Kolkhis local dev environment using Gitea's built-in Actions runner — sqlfluff lint on push, status visible in Engineering view.

**Architecture:** Gitea Actions (native CI) with act_runner in docker-compose. Backend proxies Gitea's workflow runs API to the frontend. No new auth infrastructure — uses existing admin token and a static runner registration token from `.env`.

**Tech Stack:** Gitea 1.24 (upgraded from 1.23), act_runner 0.3.0, Gitea Actions API v1, React/TypeScript frontend

---

## Decision: Gitea Actions over Woodpecker CI

Woodpecker CI was rejected because it requires OAuth-based Gitea login with no headless bootstrap — incompatible with automated cluster teardown/rebuild. Gitea Actions is native to our existing Gitea instance, supports static registration tokens via environment variables, and requires no additional auth infrastructure.

---

### Task 1: Generate runner token and add to .env

**Files:**
- Modify: `.env` (add `GITEA_RUNNER_TOKEN`)

**Step 1: Generate token**

Run: `openssl rand -hex 24`

**Step 2: Add to .env**

Append to the `# Gitea (local dev)` section in `.env`:

```
# Gitea Actions runner
GITEA_RUNNER_TOKEN=<generated-hex>
```

**Step 3: Commit**

```bash
git add .env
git commit -m "Add Gitea Actions runner registration token"
```

---

### Task 2: Upgrade Gitea to 1.24 and enable Actions

**Files:**
- Modify: `docker-compose.yml` (gitea service, lines 47-65)

**Step 1: Update Gitea image and add Actions config**

Change `gitea/gitea:1.23-rootless` → `gitea/gitea:1.24-rootless`

Add environment variables to the gitea service:
```yaml
- GITEA__actions__ENABLED=true
- GITEA_RUNNER_REGISTRATION_TOKEN=${GITEA_RUNNER_TOKEN}
```

**Step 2: Verify Gitea starts**

```bash
docker compose up -d gitea
docker compose logs gitea | tail -20
```

Expected: Gitea starts without errors on 1.24.

**Step 3: Verify Actions is enabled**

Visit `http://localhost:3000` or check:
```bash
curl -s http://localhost:3000/api/v1/version
```

Expected: Version shows 1.24.x.

**Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "Upgrade Gitea to 1.24 and enable Actions"
```

---

### Task 3: Add act_runner service to docker-compose

**Files:**
- Modify: `docker-compose.yml` (add gitea-runner service after gitea-setup, line 78)

**Step 1: Add the runner service**

```yaml
  gitea-runner:
    image: gitea/act_runner:latest
    environment:
      - GITEA_INSTANCE_URL=http://gitea:3000
      - GITEA_RUNNER_REGISTRATION_TOKEN=${GITEA_RUNNER_TOKEN}
      - GITEA_RUNNER_NAME=local-runner
      - GITEA_RUNNER_LABELS=ubuntu-latest:docker://node:20-bookworm
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
    depends_on:
      - gitea
```

Note: `GITEA_RUNNER_LABELS` maps `ubuntu-latest` (used in workflows) to a Docker image the runner uses to execute steps.

**Step 2: Start and verify**

```bash
docker compose up -d gitea-runner
docker compose logs gitea-runner | tail -20
```

Expected: Runner registers with Gitea and starts polling for jobs. Log should show "runner registered successfully" or similar.

**Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "Add Gitea Actions runner to docker-compose"
```

---

### Task 4: Add CI workflow to warehouse scaffold

**Files:**
- Modify: `backend/app/routers/orgs.py` (add to `_WAREHOUSE_SCAFFOLD` dict, line 106)

**Step 1: Add the workflow file to the scaffold**

Add this entry to `_WAREHOUSE_SCAFFOLD` before the closing `}`:

```python
    ".gitea/workflows/ci.yml": """\
name: CI
on: [push]
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install sqlfluff
      - run: sqlfluff lint models/ --dialect duckdb
""",
```

Note: Removed `actions/setup-python` — the runner label maps `ubuntu-latest` to `node:20-bookworm` which has Python available, and `pip install sqlfluff` is simpler.

**Step 2: Test by creating a new org (manual)**

Create a new org via the UI or API, then check the Gitea repo to verify `.gitea/workflows/ci.yml` exists.

**Step 3: Commit**

```bash
git add backend/app/routers/orgs.py
git commit -m "Add CI workflow to warehouse repo scaffold"
```

---

### Task 5: Add backend CI status endpoint

**Files:**
- Create: `backend/app/routers/ci.py`
- Modify: `backend/app/main.py` (register router, line 30)

**Step 1: Create CI router**

Create `backend/app/routers/ci.py`:

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import httpx

from app.auth import require_auth
from app.database import get_db
from app.gitea import _api, _headers
from app.models import OrgMembership

router = APIRouter(prefix="/api/ci")

WAREHOUSE_REPO = "warehouse"


@router.get("/status")
async def ci_status(
    auth: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Return latest CI workflow run status for the org's warehouse repo."""
    org_id = auth.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No active organization")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _api(f"/repos/{org_id}/{WAREHOUSE_REPO}/actions/runs"),
            headers=_headers(),
            params={"limit": 1},
        )
        if resp.status_code == 404:
            return {"runs": []}
        resp.raise_for_status()

    data = resp.json()
    runs = data.get("workflow_runs", [])
    if not runs:
        return {"runs": []}

    return {
        "runs": [
            {
                "id": r["id"],
                "status": r["status"],
                "conclusion": r.get("conclusion"),
                "head_sha": r.get("head_sha", "")[:8],
                "display_title": r.get("display_title", ""),
                "event": r.get("event", ""),
                "started_at": r.get("started_at"),
                "completed_at": r.get("completed_at"),
            }
            for r in runs[:5]
        ]
    }
```

**Step 2: Register the router in main.py**

Add import and include:
```python
from app.routers.ci import router as ci_router
# ...
app.include_router(ci_router)
```

**Step 3: Test the endpoint**

```bash
curl -s -H "Cookie: token=<jwt>" http://localhost:8000/api/ci/status | python -m json.tool
```

Expected: Returns `{"runs": []}` if no runs yet, or run data if CI has run.

**Step 4: Commit**

```bash
git add backend/app/routers/ci.py backend/app/main.py
git commit -m "Add CI status API endpoint"
```

---

### Task 6: Add CI status indicator to Engineering view

**Files:**
- Modify: `frontend/src/pages/ProjectEditor.tsx` (add status indicator in sidebar-tabs area)
- Modify: `frontend/src/pages/ProjectEditor.css` (add styles)

**Step 1: Add CI status state and fetch logic**

In `ProjectEditor.tsx`, add state and effect:

```typescript
const [ciStatus, setCiStatus] = useState<{
  status?: string; conclusion?: string; head_sha?: string
} | null>(null)

useEffect(() => {
  const fetchCi = async () => {
    try {
      const res = await apiFetch('/api/ci/status')
      const data = await res.json()
      if (data.runs?.length > 0) setCiStatus(data.runs[0])
      else setCiStatus(null)
    } catch { setCiStatus(null) }
  }
  fetchCi()
  const interval = setInterval(fetchCi, 30000)  // poll every 30s
  return () => clearInterval(interval)
}, [])
```

**Step 2: Add status indicator to the sidebar tabs**

Add after the refresh button in `.sidebar-tabs`:

```tsx
{ciStatus && (
  <span
    className={`ci-status-badge ci-status-${ciStatus.conclusion || ciStatus.status}`}
    title={`CI: ${ciStatus.conclusion || ciStatus.status} (${ciStatus.head_sha})`}
  >
    {ciStatus.conclusion === 'success' ? '✓' :
     ciStatus.conclusion === 'failure' ? '✗' :
     ciStatus.status === 'running' ? '◌' : '?'}
  </span>
)}
```

**Step 3: Add CSS styles**

Add to `ProjectEditor.css`:

```css
.ci-status-badge {
  font-size: 11px;
  font-weight: 600;
  padding: 1px 6px;
  border-radius: 3px;
  line-height: 1;
  flex-shrink: 0;
  cursor: default;
}

.ci-status-success { color: #629755; }
.ci-status-failure { color: #cf6679; }
.ci-status-running { color: #d4850a; }
```

**Step 4: Verify in browser**

Open the Engineering view. If a CI run has completed, the badge should show. If no runs, nothing shows (no empty state clutter).

**Step 5: Commit**

```bash
git add frontend/src/pages/ProjectEditor.tsx frontend/src/pages/ProjectEditor.css
git commit -m "Add CI status indicator to Engineering view"
```

---

## Testing the Full Flow

1. Start all services: `docker compose up -d`
2. Create a new org (or use existing one)
3. Open Engineering view, edit a model file, push from terminal
4. Watch Gitea runner pick up the job: `docker compose logs -f gitea-runner`
5. CI status badge should appear in the sidebar after ~30s (polling interval)

## Future Phases

- Phase 2: Add `dbt parse` / `dbt compile` (requires custom CI image with dbt-kolkhis)
- Phase 3: Dagster integration — deploy step triggers code location reload on merge
- Production: K8s runner deployment (similar pattern to Dagster K8s Jobs — ServiceAccount + RBAC + Deployment)
