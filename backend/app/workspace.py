"""Local git working copy + filesystem operations for project repos."""

import asyncio
import os
import shutil
from pathlib import Path
from urllib.parse import urlparse

from app.config import HOMES_PATH, GITEA_URL, GITEA_SHELL_URL, GITEA_ADMIN_USER
from app.shell import chown_recursive, get_uid_for_user


def _repo_path(org_id: str, shell_username: str, repo_name: str) -> Path:
    return Path(HOMES_PATH).resolve() / org_id / shell_username / "projects" / repo_name


def _clone_url(repo_name: str, owner: str = GITEA_ADMIN_USER) -> str:
    from app.gitea import _api_token
    parsed = urlparse(GITEA_URL)
    return f"{parsed.scheme}://{GITEA_ADMIN_USER}:{_api_token}@{parsed.netloc}/{owner}/{repo_name}.git"


def _shell_remote_url(repo_name: str, owner: str = GITEA_ADMIN_USER) -> str:
    from app.gitea import _api_token
    parsed = urlparse(GITEA_SHELL_URL)
    return f"{parsed.scheme}://{GITEA_ADMIN_USER}:{_api_token}@{parsed.netloc}/{owner}/{repo_name}.git"


def _safe_path(org_id: str, shell_username: str, repo_name: str, path: str) -> Path:
    root = _repo_path(org_id, shell_username, repo_name)
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


async def clone_repo(
    org_id: str, shell_username: str, repo_name: str,
    user_name: str = "", user_email: str = "",
    owner: str = GITEA_ADMIN_USER,
) -> None:
    dest = _repo_path(org_id, shell_username, repo_name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    await _run_git("clone", _clone_url(repo_name, owner), str(dest), cwd=dest.parent)
    # Set remote to shell-accessible URL (may differ from backend URL in local dev)
    await _run_git("remote", "set-url", "origin", _shell_remote_url(repo_name, owner), cwd=dest)
    if user_name:
        await _run_git("config", "user.name", user_name, cwd=dest)
    if user_email:
        await _run_git("config", "user.email", user_email, cwd=dest)
    # Fix ownership so the shell user can write to the repo
    uid = get_uid_for_user(org_id, shell_username)
    if uid is not None:
        chown_recursive(dest, uid, uid)


async def ensure_clone(
    org_id: str, shell_username: str, repo_name: str,
    user_name: str = "", user_email: str = "",
    owner: str = GITEA_ADMIN_USER,
) -> None:
    dest = _repo_path(org_id, shell_username, repo_name)
    if not dest.exists():
        try:
            await clone_repo(org_id, shell_username, repo_name, user_name, user_email, owner)
        except RuntimeError:
            # Race condition: another request cloned between our check and clone call
            if not dest.exists():
                raise
    # Update remote URL so the token stays current after backend restarts
    if dest.exists():
        await _run_git("remote", "set-url", "origin", _shell_remote_url(repo_name, owner), cwd=dest)


def is_clone_ready(org_id: str, shell_username: str, repo_name: str) -> bool:
    """Check if the repo clone exists on disk."""
    return _repo_path(org_id, shell_username, repo_name).exists()


def remove_repo(org_id: str, shell_username: str, repo_name: str) -> None:
    dest = _repo_path(org_id, shell_username, repo_name)
    if dest.exists():
        shutil.rmtree(dest)


def list_files(org_id: str, shell_username: str, repo_name: str, path: str = "") -> list[dict]:
    target = _safe_path(org_id, shell_username, repo_name, path)
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


def read_file(org_id: str, shell_username: str, repo_name: str, path: str) -> str:
    target = _safe_path(org_id, shell_username, repo_name, path)
    return target.read_text()


def _chown_as_user(target: Path, org_id: str, shell_username: str) -> None:
    """Set ownership of target to the shell user."""
    if os.getuid() != 0:
        return
    uid = get_uid_for_user(org_id, shell_username)
    if uid is not None:
        os.chown(target, uid, uid)


def write_file(org_id: str, shell_username: str, repo_name: str, path: str, content: str) -> None:
    target = _safe_path(org_id, shell_username, repo_name, path)
    created_dirs = _mkdirs_new(target.parent, org_id, shell_username, repo_name)
    target.write_text(content)
    _chown_as_user(target, org_id, shell_username)
    for d in created_dirs:
        _chown_as_user(d, org_id, shell_username)


def _mkdirs_new(target: Path, org_id: str, shell_username: str, repo_name: str) -> list[Path]:
    """Create parent directories and return list of newly created ones."""
    root = _repo_path(org_id, shell_username, repo_name)
    created: list[Path] = []
    parts_to_create: list[Path] = []
    current = target
    while current != root and not current.exists():
        parts_to_create.append(current)
        current = current.parent
    for d in reversed(parts_to_create):
        d.mkdir(exist_ok=True)
        created.append(d)
    return created


def create_directory(org_id: str, shell_username: str, repo_name: str, path: str) -> None:
    target = _safe_path(org_id, shell_username, repo_name, path)
    created = _mkdirs_new(target, org_id, shell_username, repo_name)
    for d in created:
        _chown_as_user(d, org_id, shell_username)


def delete_path(org_id: str, shell_username: str, repo_name: str, path: str) -> None:
    target = _safe_path(org_id, shell_username, repo_name, path)
    if not target.exists():
        raise FileNotFoundError(f"Path not found: {path}")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()


def rename_path(org_id: str, shell_username: str, repo_name: str, old_path: str, new_path: str) -> None:
    old = _safe_path(org_id, shell_username, repo_name, old_path)
    new = _safe_path(org_id, shell_username, repo_name, new_path)
    if not old.exists():
        raise FileNotFoundError(f"Path not found: {old_path}")
    if new.exists():
        raise FileExistsError(f"Path already exists: {new_path}")
    old.rename(new)


async def git_status(org_id: str, shell_username: str, repo_name: str) -> dict[str, str]:
    root = _repo_path(org_id, shell_username, repo_name)
    output = await _run_git("status", "--porcelain", cwd=root)
    result: dict[str, str] = {}
    for line in output.splitlines():
        if len(line) < 4:
            continue
        code = line[:2]
        filepath = line[3:]
        x, y = code[0], code[1]  # X=staging, Y=working tree
        if code == "??":
            result[filepath] = "untracked"
        elif y == " " and x in ("A", "M", "R"):
            # Fully staged (added, modified, or renamed) — nothing unstaged
            result[filepath] = "added"
        elif x == " " and y in ("M", "D"):
            # Unstaged changes only
            result[filepath] = "modified" if y == "M" else "deleted"
        elif x in ("A", "M", "R") and y in ("M", "D"):
            # Staged + additional unstaged changes
            result[filepath] = "modified"
        elif x == "D" or y == "D":
            result[filepath] = "deleted"
        else:
            result[filepath] = "modified"
    return result
