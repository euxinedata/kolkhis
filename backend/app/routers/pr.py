"""Pull request API — create and list PRs for the org's warehouse repo."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.database import get_db
from app.shell import ensure_shell_user
from app.workspace import git_branch, git_branch_pushed, is_clone_ready, refresh_remote
from app.gitea import create_pull_request, list_pull_requests

router = APIRouter(prefix="/api/pr")

WAREHOUSE_REPO = "warehouse"


class CreatePR(BaseModel):
    title: str


def _user_id(user: dict) -> int:
    return int(user["sub"])


async def _get_workspace(user: dict, db: AsyncSession) -> tuple[str, str, str]:
    """Return (org_id, shell_username, gitea_org) for the current user's active org."""
    org_id = user.get("org_id")
    if not org_id:
        raise HTTPException(status_code=403, detail="No organization selected")
    shell_username, gitea_org = await ensure_shell_user(
        _user_id(user), org_id, user.get("email", ""), db,
    )
    if not is_clone_ready(org_id, shell_username, WAREHOUSE_REPO):
        raise HTTPException(status_code=503, detail="Workspace is being prepared")
    await refresh_remote(org_id, shell_username, WAREHOUSE_REPO, gitea_org)
    return org_id, shell_username, gitea_org


@router.get("/branch")
async def get_branch(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Return current branch info from user's workspace clone."""
    org_id, shell_username, _ = await _get_workspace(user, db)
    branch = await git_branch(org_id, shell_username, WAREHOUSE_REPO)
    is_main = branch == "main"
    pushed = False
    if not is_main:
        pushed = await git_branch_pushed(org_id, shell_username, WAREHOUSE_REPO, branch)
    return {"branch": branch, "is_main": is_main, "pushed": pushed}


@router.get("/list")
async def list_prs(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Return open PRs for the org's warehouse repo."""
    org_id, _, _ = await _get_workspace(user, db)
    prs = await list_pull_requests(WAREHOUSE_REPO, state="open", owner=org_id)
    return [
        {
            "number": pr["number"],
            "title": pr["title"],
            "head": pr.get("head", {}).get("ref", ""),
            "state": pr["state"],
            "user": pr.get("user", {}).get("login", ""),
            "url": pr.get("html_url", ""),
        }
        for pr in prs
    ]


@router.post("/create")
async def create_pr(
    body: CreatePR,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    """Create a PR from user's current branch to main."""
    org_id, shell_username, _ = await _get_workspace(user, db)
    branch = await git_branch(org_id, shell_username, WAREHOUSE_REPO)
    if branch == "main":
        raise HTTPException(status_code=400, detail="Switch to a feature branch first")
    pushed = await git_branch_pushed(org_id, shell_username, WAREHOUSE_REPO, branch)
    if not pushed:
        raise HTTPException(status_code=400, detail="Push your branch first")

    # Check if PR already exists for this branch
    existing = await list_pull_requests(WAREHOUSE_REPO, state="open", owner=org_id)
    for pr in existing:
        if pr.get("head", {}).get("ref") == branch:
            raise HTTPException(
                status_code=409,
                detail=f"PR already exists for this branch: #{pr['number']}",
            )

    pr = await create_pull_request(
        WAREHOUSE_REPO, title=body.title, head=branch, base="main", owner=org_id,
    )
    return {"number": pr["number"], "title": pr["title"], "url": pr.get("html_url", "")}
