import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.database import get_db
from app.gitea import create_repo, create_or_update_file, delete_repo
from app.models import Project
from app.workspace import (
    clone_repo, ensure_clone, remove_repo,
    list_files as ws_list_files, read_file, write_file,
    create_directory, rename_path, git_status,
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
""",
    "profiles.yml": """\
'{name}':
  target: dev
  outputs:
    dev:
      type: duckdb
      path: ':memory:'
""",
    "models/.gitkeep": "",
    "models/staging/.gitkeep": "",
    "models/marts/.gitkeep": "",
    "seeds/.gitkeep": "",
    "macros/.gitkeep": "",
    "tests/.gitkeep": "",
}


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


@router.get("")
async def list_projects(
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_auth),
):
    result = await db.execute(select(Project).order_by(Project.created_at.desc()))
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
    _user: dict = Depends(require_auth),
):
    # Check for duplicate name
    result = await db.execute(select(Project).where(Project.name == body.name))
    if result.scalar() is not None:
        raise HTTPException(status_code=409, detail=f"Project '{body.name}' already exists")

    # Create Gitea repo
    repo_name = body.name.lower().replace(" ", "-")
    try:
        await create_repo(repo_name)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to create repo: {exc}")

    # Commit dbt scaffold via Gitea API (so initial clone is clean)
    for path, content in DBT_SCAFFOLD.items():
        rendered = content.replace("{name}", body.name)
        await create_or_update_file(repo_name, path, rendered, f"Add {path}")

    # Clone locally
    await clone_repo(repo_name)

    # Save to DB
    project = Project(
        id=str(uuid.uuid4()),
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
    _user: dict = Depends(require_auth),
):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        await delete_repo(project.gitea_repo_name)
    except Exception:
        pass  # Repo may already be gone

    remove_repo(project.gitea_repo_name)

    await db.delete(project)
    await db.commit()
    return {"deleted": project.id}


async def _get_project(project_id: str, db: AsyncSession) -> Project:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/{project_id}/files")
async def list_project_files(
    project_id: str,
    path: str = "",
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_auth),
):
    project = await _get_project(project_id, db)
    await ensure_clone(project.gitea_repo_name)
    try:
        entries = ws_list_files(project.gitea_repo_name, path)
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
    _user: dict = Depends(require_auth),
):
    project = await _get_project(project_id, db)
    await ensure_clone(project.gitea_repo_name)
    try:
        content = read_file(project.gitea_repo_name, path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    return {"path": path, "content": content}


@router.post("/{project_id}/files")
async def create_file(
    project_id: str,
    body: CreateFile,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_auth),
):
    project = await _get_project(project_id, db)
    await ensure_clone(project.gitea_repo_name)
    try:
        write_file(project.gitea_repo_name, body.path, body.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"path": body.path}


@router.post("/{project_id}/folders")
async def create_folder(
    project_id: str,
    body: CreateFolder,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_auth),
):
    project = await _get_project(project_id, db)
    await ensure_clone(project.gitea_repo_name)
    try:
        create_directory(project.gitea_repo_name, body.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"path": body.path}


@router.post("/{project_id}/rename")
async def rename_item(
    project_id: str,
    body: RenameItem,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_auth),
):
    project = await _get_project(project_id, db)
    await ensure_clone(project.gitea_repo_name)
    try:
        rename_path(project.gitea_repo_name, body.old_path, body.new_path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"old_path": body.old_path, "new_path": body.new_path}


@router.get("/{project_id}/status")
async def get_project_status(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_auth),
):
    project = await _get_project(project_id, db)
    await ensure_clone(project.gitea_repo_name)
    return await git_status(project.gitea_repo_name)
