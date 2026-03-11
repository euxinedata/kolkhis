import logging

from fastapi import APIRouter, Depends, HTTPException

import httpx

from app.auth import require_auth
from app.config import GITEA_URL, GITEA_ADMIN_USER, GITEA_ADMIN_PASSWORD
from app.gitea import _api, _headers

router = APIRouter(prefix="/api/ci")
logger = logging.getLogger(__name__)

WAREHOUSE_REPO = "warehouse"

# Cached Gitea web session (cookies + CSRF token)
_gitea_session: dict | None = None


async def _ensure_gitea_session() -> dict:
    """Login to Gitea web UI and cache session cookies + CSRF token.

    The REST API doesn't expose step-level data for action runs.
    Gitea's internal web endpoint does, but requires cookie auth + CSRF.
    """
    global _gitea_session
    if _gitea_session is not None:
        return _gitea_session

    async with httpx.AsyncClient(follow_redirects=True) as client:
        # Get login page for CSRF token
        resp = await client.get(f"{GITEA_URL}/user/login")
        resp.raise_for_status()
        csrf = ""
        for line in resp.text.splitlines():
            if 'csrfToken:' in line:
                csrf = line.split("'")[1]
                break

        # Login
        resp = await client.post(
            f"{GITEA_URL}/user/login",
            data={
                "_csrf": csrf,
                "user_name": GITEA_ADMIN_USER,
                "password": GITEA_ADMIN_PASSWORD,
            },
        )
        resp.raise_for_status()

        # Extract CSRF from the response page
        new_csrf = ""
        for line in resp.text.splitlines():
            if 'csrfToken:' in line:
                new_csrf = line.split("'")[1]
                break

        _gitea_session = {
            "cookies": dict(client.cookies),
            "csrf": new_csrf,
        }
        return _gitea_session


def _invalidate_session() -> None:
    global _gitea_session
    _gitea_session = None


@router.get("/status")
async def ci_status(
    auth: dict = Depends(require_auth),
):
    """Return latest CI workflow run status for the org's warehouse repo."""
    org_id = auth.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No active organization")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _api(f"/repos/{org_id}/{WAREHOUSE_REPO}/actions/tasks"),
            headers=_headers(),
            params={"limit": 20},
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
                "name": r.get("name", ""),
                "status": r["status"],
                "head_branch": r.get("head_branch", ""),
                "head_sha": r.get("head_sha", "")[:8],
                "display_title": r.get("display_title", ""),
                "event": r.get("event", ""),
                "started_at": r.get("started_at"),
                "completed_at": r.get("completed_at"),
                "run_number": r.get("run_number", 0),
            }
            for r in runs[:10]
        ]
    }


async def _fetch_job_data(
    org_id: str, run_number: int, job_index: int,
) -> dict | None:
    """Fetch step-level data from Gitea's internal web endpoint.

    Two-phase: first get step metadata, then request logs for each step.
    """
    session = await _ensure_gitea_session()
    url = f"{GITEA_URL}/{org_id}/{WAREHOUSE_REPO}/actions/runs/{run_number}/jobs/{job_index}"
    headers = {
        "Content-Type": "application/json",
        "X-Csrf-Token": session["csrf"],
    }

    async with httpx.AsyncClient() as client:
        # Phase 1: get step metadata (no log cursors)
        resp = await client.post(
            url, cookies=session["cookies"], headers=headers,
            json={"logCursors": []},
        )
        if resp.status_code in (401, 403):
            _invalidate_session()
            return None
        if resp.status_code != 200:
            return None
        data = resp.json()

        # Phase 2: request logs for each step
        step_count = len(data.get("state", {}).get("currentJob", {}).get("steps", []))
        if step_count > 0:
            cursors = [
                {"step": i, "cursor": 0, "expanded": True}
                for i in range(step_count)
            ]
            resp = await client.post(
                url, cookies=session["cookies"], headers=headers,
                json={"logCursors": cursors},
            )
            if resp.status_code == 200:
                data = resp.json()

        return data


@router.get("/logs/{task_id}")
async def ci_logs(
    task_id: int,
    auth: dict = Depends(require_auth),
):
    """Return structured step data for a CI task."""
    org_id = auth.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No active organization")

    # Get run_number and job name for this task from the tasks list
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _api(f"/repos/{org_id}/{WAREHOUSE_REPO}/actions/tasks"),
            headers=_headers(),
            params={"limit": 20},
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=404, detail="Task not found")

    tasks = resp.json().get("workflow_runs", [])
    task = None
    for t in tasks:
        if t["id"] == task_id:
            task = t
            break
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    run_number = task["run_number"]
    job_name = task["name"]

    # Find the right job index for this task within the run.
    # A run can have multiple jobs (e.g. lint=0, deploy=1).
    # We try job indices and match by job title.
    for job_index in range(10):
        data = await _fetch_job_data(org_id, run_number, job_index)
        if data is None:
            continue
        current_job = data.get("state", {}).get("currentJob", {})
        if current_job.get("title", "").lower() == job_name.lower():
            break
    else:
        raise HTTPException(status_code=404, detail="Job not found")

    # Build response
    steps_data = current_job.get("steps", [])
    logs_data = data.get("logs", {}).get("stepsLog", [])

    # Index logs by step number
    logs_by_step: dict[int, list[str]] = {}
    for entry in logs_data:
        step_idx = entry.get("step", -1)
        lines = [
            line.get("message", "")
            for line in entry.get("lines", [])
        ]
        logs_by_step[step_idx] = lines

    steps = []
    for i, step in enumerate(steps_data):
        summary = step.get("summary", "")
        # Clean up multi-line run: commands (keep first line only)
        if "\n" in summary:
            summary = summary.split("\n")[0].strip()
        steps.append({
            "name": summary,
            "status": step.get("status", "unknown"),
            "duration": step.get("duration", ""),
            "lines": logs_by_step.get(i, []),
        })

    return {"steps": steps}
