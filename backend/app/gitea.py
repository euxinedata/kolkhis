"""Thin async client for Gitea's REST API v1."""

import base64
import logging

import httpx

from app.config import GITEA_URL, GITEA_ADMIN_USER, GITEA_ADMIN_PASSWORD

logger = logging.getLogger(__name__)

_api_token: str | None = None


def _headers() -> dict[str, str]:
    if _api_token is None:
        raise RuntimeError("Gitea API token not initialized — call bootstrap_token() first")
    return {"Authorization": f"token {_api_token}"}


def _api(path: str) -> str:
    return f"{GITEA_URL}/api/v1{path}"


async def bootstrap_token() -> None:
    """Create an API token for the admin user (idempotent).

    Uses basic auth to create a token named 'kolkhis'. If the token already
    exists, deletes and recreates it so we always have the value.
    """
    global _api_token
    token_name = "kolkhis"
    basic_auth = (GITEA_ADMIN_USER, GITEA_ADMIN_PASSWORD)

    async with httpx.AsyncClient() as client:
        # List existing tokens
        resp = await client.get(
            _api(f"/users/{GITEA_ADMIN_USER}/tokens"),
            auth=basic_auth,
        )
        resp.raise_for_status()
        for tok in resp.json():
            if tok["name"] == token_name:
                # Delete stale token so we can recreate with known value
                await client.delete(
                    _api(f"/users/{GITEA_ADMIN_USER}/tokens/{tok['id']}"),
                    auth=basic_auth,
                )
                break

        # Create new token
        resp = await client.post(
            _api(f"/users/{GITEA_ADMIN_USER}/tokens"),
            auth=basic_auth,
            json={"name": token_name, "scopes": ["all"]},
        )
        resp.raise_for_status()
        _api_token = resp.json()["sha1"]
        logger.info("Gitea API token bootstrapped for user %s", GITEA_ADMIN_USER)


# ── Organization operations ──────────────────────────────────────────


async def create_gitea_org(name: str) -> dict:
    """Create a Gitea organization (idempotent)."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _api("/orgs"),
            headers=_headers(),
            json={"username": name, "visibility": "private"},
        )
        if resp.status_code == 422:
            # Org already exists — fetch and return it
            resp = await client.get(_api(f"/orgs/{name}"), headers=_headers())
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return resp.json()


async def delete_gitea_org(name: str) -> None:
    """Delete a Gitea organization."""
    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            _api(f"/orgs/{name}"),
            headers=_headers(),
        )
        resp.raise_for_status()


# ── Repository operations ────────────────────────────────────────────


async def create_repo(name: str, owner: str = GITEA_ADMIN_USER) -> dict:
    """Create a repo (idempotent). If owner differs from admin user, creates under that org."""
    if owner == GITEA_ADMIN_USER:
        url = _api("/user/repos")
    else:
        url = _api(f"/orgs/{owner}/repos")
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            url,
            headers=_headers(),
            json={"name": name, "auto_init": True, "default_branch": "main"},
        )
        if resp.status_code == 409:
            # Repo already exists — fetch and return it
            resp = await client.get(_api(f"/repos/{owner}/{name}"), headers=_headers())
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        return resp.json()


async def delete_repo(name: str, owner: str = GITEA_ADMIN_USER) -> None:
    async with httpx.AsyncClient() as client:
        resp = await client.delete(
            _api(f"/repos/{owner}/{name}"),
            headers=_headers(),
        )
        resp.raise_for_status()


async def list_repos(owner: str = GITEA_ADMIN_USER) -> list[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _api(f"/orgs/{owner}/repos") if owner != GITEA_ADMIN_USER else _api("/user/repos"),
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()


# ── File operations ──────────────────────────────────────────────────


async def get_file(repo: str, path: str, ref: str = "main", owner: str = GITEA_ADMIN_USER) -> str:
    """Return decoded file content."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _api(f"/repos/{owner}/{repo}/contents/{path}"),
            headers=_headers(),
            params={"ref": ref},
        )
        resp.raise_for_status()
        return base64.b64decode(resp.json()["content"]).decode()


async def create_files_batch(
    repo: str, files: dict[str, str], message: str,
    branch: str = "main", owner: str = GITEA_ADMIN_USER,
) -> dict:
    """Create multiple files in a single commit."""
    payload = {
        "branch": branch,
        "message": message,
        "files": [
            {
                "operation": "create",
                "path": path,
                "content": base64.b64encode(content.encode()).decode(),
            }
            for path, content in files.items()
        ],
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _api(f"/repos/{owner}/{repo}/contents"),
            headers=_headers(),
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()


async def create_or_update_file(
    repo: str, path: str, content: str, message: str,
    branch: str = "main", owner: str = GITEA_ADMIN_USER,
) -> dict:
    """Create or update a file. Fetches SHA automatically for updates."""
    encoded = base64.b64encode(content.encode()).decode()
    payload: dict = {"content": encoded, "message": message, "branch": branch}

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _api(f"/repos/{owner}/{repo}/contents/{path}"),
            headers=_headers(),
            params={"ref": branch},
        )
        if resp.status_code == 200:
            payload["sha"] = resp.json()["sha"]
            method = client.put
        else:
            method = client.post

        resp = await method(
            _api(f"/repos/{owner}/{repo}/contents/{path}"),
            headers=_headers(),
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()


async def delete_file(
    repo: str, path: str, message: str,
    branch: str = "main", owner: str = GITEA_ADMIN_USER,
) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _api(f"/repos/{owner}/{repo}/contents/{path}"),
            headers=_headers(),
            params={"ref": branch},
        )
        resp.raise_for_status()
        sha = resp.json()["sha"]

        resp = await client.delete(
            _api(f"/repos/{owner}/{repo}/contents/{path}"),
            headers=_headers(),
            json={"message": message, "sha": sha, "branch": branch},
        )
        resp.raise_for_status()
        return resp.json()


async def list_files(repo: str, path: str = "", ref: str = "main", owner: str = GITEA_ADMIN_USER) -> list[dict]:
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            _api(f"/repos/{owner}/{repo}/contents/{path}"),
            headers=_headers(),
            params={"ref": ref},
        )
        resp.raise_for_status()
        return resp.json()


# ── Branch operations ────────────────────────────────────────────────


async def create_branch(repo: str, name: str, from_ref: str = "main", owner: str = GITEA_ADMIN_USER) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _api(f"/repos/{owner}/{repo}/branches"),
            headers=_headers(),
            json={"new_branch_name": name, "old_branch_name": from_ref},
        )
        resp.raise_for_status()
        return resp.json()


# ── Pull request operations ──────────────────────────────────────────


async def create_pull_request(
    repo: str, title: str, head: str, base: str = "main", owner: str = GITEA_ADMIN_USER,
) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _api(f"/repos/{owner}/{repo}/pulls"),
            headers=_headers(),
            json={"title": title, "head": head, "base": base},
        )
        resp.raise_for_status()
        return resp.json()


async def merge_pull_request(repo: str, pr_number: int, owner: str = GITEA_ADMIN_USER) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _api(f"/repos/{owner}/{repo}/pulls/{pr_number}/merge"),
            headers=_headers(),
            json={"Do": "merge"},
        )
        resp.raise_for_status()
        return resp.json()
