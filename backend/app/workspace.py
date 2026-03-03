"""Local git working copy + filesystem operations for project repos."""

import asyncio
import os
import shutil
from pathlib import Path
from urllib.parse import urlparse

from app.config import PROJECTS_PATH, GITEA_URL, GITEA_ADMIN_USER


def _repo_path(repo_name: str) -> Path:
    return Path(PROJECTS_PATH).resolve() / repo_name


def _clone_url(repo_name: str) -> str:
    from app.gitea import _api_token
    parsed = urlparse(GITEA_URL)
    return f"{parsed.scheme}://{GITEA_ADMIN_USER}:{_api_token}@{parsed.netloc}/{GITEA_ADMIN_USER}/{repo_name}.git"


def _safe_path(repo_name: str, path: str) -> Path:
    root = _repo_path(repo_name)
    resolved = (root / path).resolve()
    if not str(resolved).startswith(str(root.resolve())):
        raise ValueError("Path traversal detected")
    return resolved


async def _run_git(*args: str, cwd: Path) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {stderr.decode().strip()}")
    return stdout.decode()


async def clone_repo(repo_name: str) -> None:
    dest = _repo_path(repo_name)
    await _run_git("clone", _clone_url(repo_name), str(dest), cwd=dest.parent)


async def ensure_clone(repo_name: str) -> None:
    if not _repo_path(repo_name).exists():
        await clone_repo(repo_name)


def remove_repo(repo_name: str) -> None:
    dest = _repo_path(repo_name)
    if dest.exists():
        shutil.rmtree(dest)


def list_files(repo_name: str, path: str = "") -> list[dict]:
    target = _safe_path(repo_name, path)
    if not target.is_dir():
        raise FileNotFoundError(f"Directory not found: {path}")
    entries = []
    for entry in os.scandir(target):
        if entry.name == ".git":
            continue
        entries.append({
            "name": entry.name,
            "path": f"{path}/{entry.name}".lstrip("/"),
            "type": "dir" if entry.is_dir() else "file",
            "size": entry.stat().st_size if entry.is_file() else 0,
        })
    return entries


def read_file(repo_name: str, path: str) -> str:
    target = _safe_path(repo_name, path)
    return target.read_text()


def write_file(repo_name: str, path: str, content: str) -> None:
    target = _safe_path(repo_name, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def create_directory(repo_name: str, path: str) -> None:
    target = _safe_path(repo_name, path)
    target.mkdir(parents=True, exist_ok=True)


def delete_path(repo_name: str, path: str) -> None:
    target = _safe_path(repo_name, path)
    if not target.exists():
        raise FileNotFoundError(f"Path not found: {path}")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()


def rename_path(repo_name: str, old_path: str, new_path: str) -> None:
    old = _safe_path(repo_name, old_path)
    new = _safe_path(repo_name, new_path)
    if not old.exists():
        raise FileNotFoundError(f"Path not found: {old_path}")
    if new.exists():
        raise FileExistsError(f"Path already exists: {new_path}")
    old.rename(new)


async def git_status(repo_name: str) -> dict[str, str]:
    root = _repo_path(repo_name)
    output = await _run_git("status", "--porcelain", cwd=root)
    result: dict[str, str] = {}
    for line in output.splitlines():
        if len(line) < 4:
            continue
        code = line[:2]
        filepath = line[3:]
        if code in ("??", "A ", " A"):
            result[filepath] = "new"
        elif code in ("M ", " M", "MM"):
            result[filepath] = "modified"
        elif code in ("D ", " D"):
            result[filepath] = "deleted"
        else:
            result[filepath] = "modified"
    return result
