"""Workspace file API — org-scoped, always operates on the warehouse repo."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.database import get_db
from app.shell import ensure_shell_user
from app.workspace import (
    list_files as ws_list_files, read_file, write_file,
    create_directory, rename_path, delete_path, git_status,
    is_clone_ready, refresh_remote,
)

router = APIRouter(prefix="/api/workspace")

WAREHOUSE_REPO = "warehouse"


class CreateFile(BaseModel):
    path: str
    content: str


class CreateFolder(BaseModel):
    path: str


class RenameItem(BaseModel):
    old_path: str
    new_path: str


class DeleteItem(BaseModel):
    path: str


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


@router.get("/files")
async def list_workspace_files(
    path: str = "",
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id, shell_username, _ = await _get_workspace(user, db)
    try:
        entries = ws_list_files(org_id, shell_username, WAREHOUSE_REPO, path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Directory not found: {path}")
    return [
        {"name": e["name"], "path": e["path"], "type": e["type"], "size": e.get("size", 0)}
        for e in entries
        if e["name"] != ".gitkeep"
    ]


@router.get("/file")
async def get_workspace_file(
    path: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id, shell_username, _ = await _get_workspace(user, db)
    try:
        content = read_file(org_id, shell_username, WAREHOUSE_REPO, path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    return {"path": path, "content": content}


@router.post("/files")
async def create_workspace_file(
    body: CreateFile,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id, shell_username, _ = await _get_workspace(user, db)
    try:
        write_file(org_id, shell_username, WAREHOUSE_REPO, body.path, body.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"path": body.path}


@router.post("/folders")
async def create_workspace_folder(
    body: CreateFolder,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id, shell_username, _ = await _get_workspace(user, db)
    try:
        create_directory(org_id, shell_username, WAREHOUSE_REPO, body.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"path": body.path}


@router.post("/rename")
async def rename_workspace_item(
    body: RenameItem,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id, shell_username, _ = await _get_workspace(user, db)
    try:
        rename_path(org_id, shell_username, WAREHOUSE_REPO, body.old_path, body.new_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"old_path": body.old_path, "new_path": body.new_path}


@router.delete("/files")
async def delete_workspace_file(
    body: DeleteItem,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id, shell_username, _ = await _get_workspace(user, db)
    try:
        delete_path(org_id, shell_username, WAREHOUSE_REPO, body.path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"deleted": body.path}


@router.get("/status")
async def get_workspace_status(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    org_id, shell_username, _ = await _get_workspace(user, db)
    return await git_status(org_id, shell_username, WAREHOUSE_REPO)
