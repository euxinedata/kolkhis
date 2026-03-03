import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.config import WORKER_AUTH_TOKEN
from app.database import get_db
from app.gitea import create_repo, create_or_update_file, delete_repo
from app.models import Project
from app.shell import ensure_shell_user
from app.workspace import (
    clone_repo, ensure_clone, remove_repo,
    list_files as ws_list_files, read_file, write_file,
    create_directory, rename_path, delete_path, git_status,
)

router = APIRouter(prefix="/api/projects")

DBT_SCAFFOLD = {
    "dbt_project.yml": """\
name: '{name}'
version: '1.0.0'
config-version: 2
profile: '{name}'
model-paths: ["models"]
seed-paths: ["seeds"]
macro-paths: ["macros"]
test-paths: ["tests"]

dispatch:
  - macro_namespace: dbt
    search_order: ['kolkhis', '{name}', 'dbt']
""",
    "profiles.yml": """\
'{name}':
  target: dev
  outputs:
    dev:
      type: kolkhis
      backend_url: http://host.docker.internal:8000
      worker_url: http://host.docker.internal:8080
      auth_token: '{worker_auth_token}'
      database: kolkhis
      schema: main
""",
}

DBT_DIRS = ["macros", "models", "seeds", "tests"]


class CreateProject(BaseModel):
    name: str
    description: str = ""


class CreateFile(BaseModel):
    path: str
    content: str = ""


class CreateFolder(BaseModel):
    path: str


class RenameItem(BaseModel):
    old_path: str
    new_path: str


class DeleteItem(BaseModel):
    path: str


def _user_id(user: dict) -> int:
    return int(user["sub"])


def _user_git(user: dict) -> dict:
    return {"user_name": user.get("name", ""), "user_email": user.get("email", "")}


async def _get_shell_username(user: dict, db: AsyncSession) -> str:
    """Get or provision the shell username for the current user."""
    return await ensure_shell_user(
        _user_id(user), user.get("email", ""), db
    )


@router.get("")
async def list_projects(
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    uid = _user_id(user)
    result = await db.execute(
        select(Project).where(Project.user_id == uid).order_by(Project.created_at.desc())
    )
    return [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "created_at": p.created_at.isoformat(),
        }
        for p in result.scalars().all()
    ]


@router.post("")
async def create_project(
    body: CreateProject,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    uid = _user_id(user)

    # Check for duplicate name per user
    result = await db.execute(
        select(Project).where(Project.user_id == uid, Project.name == body.name)
    )
    if result.scalar() is not None:
        raise HTTPException(status_code=409, detail=f"Project '{body.name}' already exists")

    # Ensure shell user is provisioned
    shell_username = await _get_shell_username(user, db)

    # Create Gitea repo
    repo_name = body.name.lower().replace(" ", "-")
    try:
        await create_repo(repo_name)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            raise HTTPException(status_code=409, detail=f"Project '{body.name}' already exists")
        raise HTTPException(status_code=502, detail=f"Failed to create repo: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to create repo: {exc}")

    # Commit dbt scaffold via Gitea API (so initial clone is clean)
    for path, content in DBT_SCAFFOLD.items():
        dbt_name = body.name.replace("-", "_")
        rendered = content.replace("{name}", dbt_name).replace("{worker_auth_token}", WORKER_AUTH_TOKEN)
        await create_or_update_file(repo_name, path, rendered, f"Add {path}")

    # Clone locally and create dbt directories
    await clone_repo(shell_username, repo_name, **_user_git(user))
    for d in DBT_DIRS:
        create_directory(shell_username, repo_name, d)

    # Save to DB
    project = Project(
        id=str(uuid.uuid4()),
        user_id=uid,
        name=body.name,
        description=body.description,
        gitea_repo_name=repo_name,
    )
    db.add(project)
    await db.commit()

    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "created_at": project.created_at.isoformat(),
    }


@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    uid = _user_id(user)
    project = await _get_project(project_id, uid, db)
    shell_username = await _get_shell_username(user, db)

    try:
        await delete_repo(project.gitea_repo_name)
    except Exception:
        pass  # Repo may already be gone

    remove_repo(shell_username, project.gitea_repo_name)

    await db.delete(project)
    await db.commit()
    return {"deleted": project.id}


async def _get_project(project_id: str, user_id: int, db: AsyncSession) -> Project:
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.user_id == user_id)
    )
    project = result.scalar()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/{project_id}/files")
async def list_project_files(
    project_id: str,
    path: str = "",
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    uid = _user_id(user)
    project = await _get_project(project_id, uid, db)
    shell_username = await _get_shell_username(user, db)
    await ensure_clone(shell_username, project.gitea_repo_name, **_user_git(user))
    try:
        entries = ws_list_files(shell_username, project.gitea_repo_name, path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Directory not found: {path}")
    return [
        {"name": e["name"], "path": e["path"], "type": e["type"], "size": e.get("size", 0)}
        for e in entries
        if e["name"] != ".gitkeep"
    ]


@router.get("/{project_id}/file")
async def get_project_file(
    project_id: str,
    path: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    uid = _user_id(user)
    project = await _get_project(project_id, uid, db)
    shell_username = await _get_shell_username(user, db)
    await ensure_clone(shell_username, project.gitea_repo_name, **_user_git(user))
    try:
        content = read_file(shell_username, project.gitea_repo_name, path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    return {"path": path, "content": content}


@router.post("/{project_id}/files")
async def create_file(
    project_id: str,
    body: CreateFile,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    uid = _user_id(user)
    project = await _get_project(project_id, uid, db)
    shell_username = await _get_shell_username(user, db)
    await ensure_clone(shell_username, project.gitea_repo_name, **_user_git(user))
    try:
        write_file(shell_username, project.gitea_repo_name, body.path, body.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"path": body.path}


@router.post("/{project_id}/folders")
async def create_folder(
    project_id: str,
    body: CreateFolder,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    uid = _user_id(user)
    project = await _get_project(project_id, uid, db)
    shell_username = await _get_shell_username(user, db)
    await ensure_clone(shell_username, project.gitea_repo_name, **_user_git(user))
    try:
        create_directory(shell_username, project.gitea_repo_name, body.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"path": body.path}


@router.post("/{project_id}/rename")
async def rename_item(
    project_id: str,
    body: RenameItem,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    uid = _user_id(user)
    project = await _get_project(project_id, uid, db)
    shell_username = await _get_shell_username(user, db)
    await ensure_clone(shell_username, project.gitea_repo_name, **_user_git(user))
    try:
        rename_path(shell_username, project.gitea_repo_name, body.old_path, body.new_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"old_path": body.old_path, "new_path": body.new_path}


@router.delete("/{project_id}/files")
async def delete_file(
    project_id: str,
    body: DeleteItem,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    uid = _user_id(user)
    project = await _get_project(project_id, uid, db)
    shell_username = await _get_shell_username(user, db)
    await ensure_clone(shell_username, project.gitea_repo_name, **_user_git(user))
    try:
        delete_path(shell_username, project.gitea_repo_name, body.path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"deleted": body.path}


@router.get("/{project_id}/status")
async def get_project_status(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_auth),
):
    uid = _user_id(user)
    project = await _get_project(project_id, uid, db)
    shell_username = await _get_shell_username(user, db)
    await ensure_clone(shell_username, project.gitea_repo_name, **_user_git(user))
    return await git_status(shell_username, project.gitea_repo_name)
