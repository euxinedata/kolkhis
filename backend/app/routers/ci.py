from fastapi import APIRouter, Depends, HTTPException

import httpx

from app.auth import require_auth
from app.gitea import _api, _headers

router = APIRouter(prefix="/api/ci")

WAREHOUSE_REPO = "warehouse"


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
            params={"limit": 5},
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
                "head_sha": r.get("head_sha", "")[:8],
                "display_title": r.get("display_title", ""),
                "event": r.get("event", ""),
                "started_at": r.get("started_at"),
                "completed_at": r.get("completed_at"),
            }
            for r in runs[:5]
        ]
    }
