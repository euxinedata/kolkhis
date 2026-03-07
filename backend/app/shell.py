"""Shell container user provisioning via direct filesystem writes."""

import asyncio
import fcntl
import logging
import os
import re
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import jwt

from app.config import HOMES_PATH, JWT_SECRET, SHELL_BACKEND_URL, SHELL_SSH_PUBKEY_PATH
from app.models import OrgMembership

log = logging.getLogger(__name__)

def _auth_dir(org_id: str) -> Path:
    return Path(HOMES_PATH) / org_id / ".auth"


def generate_shell_username(email: str) -> str:
    """Derive a Linux username from an email address."""
    local = email.split("@")[0].lower()
    username = re.sub(r"[^a-z0-9]+", "-", local).strip("-")
    return username[:32] if username else "user"


def _user_exists(org_id: str, username: str) -> bool:
    """Check if a user already exists in the auth passwd file."""
    passwd_file = _auth_dir(org_id) / "passwd"
    if not passwd_file.exists():
        return False
    for line in passwd_file.read_text().splitlines():
        if line.split(":")[0] == username:
            return True
    return False


def _next_uid(org_id: str) -> int:
    """Find the next available UID >= 1000 (excluding system UIDs like nobody=65534)."""
    passwd_file = _auth_dir(org_id) / "passwd"
    max_uid = 999
    if passwd_file.exists():
        for line in passwd_file.read_text().splitlines():
            parts = line.split(":")
            if len(parts) >= 3:
                uid = int(parts[2])
                if 1000 <= uid < 60000 and uid > max_uid:
                    max_uid = uid
    return max_uid + 1


def get_uid_for_user(org_id: str, username: str) -> int | None:
    """Get the UID for a given username, or None if not found."""
    passwd_file = _auth_dir(org_id) / "passwd"
    if not passwd_file.exists():
        return None
    for line in passwd_file.read_text().splitlines():
        parts = line.split(":")
        if parts[0] == username and len(parts) >= 3:
            return int(parts[2])
    return None


def chown_recursive(path: Path, uid: int, gid: int) -> None:
    """Recursively chown a directory. Skipped when not running as root."""
    if os.getuid() != 0:
        return
    os.chown(path, uid, gid)
    for dirpath, dirnames, filenames in os.walk(path):
        for name in dirnames + filenames:
            os.chown(os.path.join(dirpath, name), uid, gid)


def _make_shell_token(user_id: int, email: str, org_id: str) -> str:
    """Create a long-lived JWT for dbt CLI usage in the shell."""
    return jwt.encode(
        {"sub": str(user_id), "email": email, "name": email.split("@")[0], "org_id": org_id},
        JWT_SECRET,
        algorithm="HS256",
    )


def provision_shell_user(org_id: str, shell_username: str, user_id: int = 0, email: str = "") -> None:
    """Create a Linux user by writing directly to auth files on the PV."""
    auth_dir = _auth_dir(org_id)
    lock_path = auth_dir / ".lock"
    auth_dir.mkdir(parents=True, exist_ok=True)

    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            if _user_exists(org_id, shell_username):
                log.info("Shell user already exists: %s", shell_username)
                return

            uid = _next_uid(org_id)
            gid = uid  # one group per user

            # Append to passwd
            with open(auth_dir / "passwd", "a") as f:
                f.write(f"{shell_username}:x:{uid}:{gid}::/home/{shell_username}:/bin/bash\n")

            # Append to shadow (locked password — SSH key only)
            with open(auth_dir / "shadow", "a") as f:
                f.write(f"{shell_username}:!:19000:0:99999:7:::\n")

            # Append to group
            with open(auth_dir / "group", "a") as f:
                f.write(f"{shell_username}:x:{gid}:\n")

            # Append to gshadow
            with open(auth_dir / "gshadow", "a") as f:
                f.write(f"{shell_username}:!::\n")
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)

    # Create home directory structure
    home = Path(HOMES_PATH) / org_id / shell_username
    for subdir in [".ssh", ".dbt", "projects"]:
        (home / subdir).mkdir(parents=True, exist_ok=True)

    # Copy SSH public key
    ssh_dir = home / ".ssh"
    ssh_dir.chmod(0o700)
    pubkey_src = Path(SHELL_SSH_PUBKEY_PATH)
    if pubkey_src.exists():
        ak = ssh_dir / "authorized_keys"
        shutil.copy2(pubkey_src, ak)
        ak.chmod(0o600)

    # Minimal skel files
    bashrc = home / ".bashrc"
    if not bashrc.exists():
        # Generate dbt auth token if user info is available
        token_lines = ""
        if user_id and email:
            token = _make_shell_token(user_id, email, org_id)
            token_lines = (
                "\n# Kolkhis dbt adapter\n"
                f"export KOLKHIS_AUTH_TOKEN='{token}'\n"
                f"export KOLKHIS_BACKEND_URL='{SHELL_BACKEND_URL}'\n"
                f"export DBT_USER='{shell_username.replace('-', '_')}'\n"
            )
        bashrc.write_text(
            "# ~/.bashrc\n"
            "[ -f /etc/bash.bashrc ] && . /etc/bash.bashrc\n"
            "\n"
            "# Colored prompt: user@host:dir$\n"
            "PS1='\\[\\033[01;32m\\]\\u@\\h\\[\\033[00m\\]:\\[\\033[01;34m\\]\\w\\[\\033[00m\\]\\$ '\n"
            "\n"
            "# Colors for ls and grep\n"
            "alias ls='ls --color=auto'\n"
            "alias grep='grep --color=auto'\n"
            + token_lines
        )

    profile = home / ".profile"
    if not profile.exists():
        profile.write_text('# ~/.profile\n[ -f "$HOME/.bashrc" ] && . "$HOME/.bashrc"\n')

    # Seed dbt profiles
    profiles_yml = home / ".dbt" / "profiles.yml"
    if not profiles_yml.exists():
        profiles_yml.write_text("# dbt profiles — managed by Kolkhis\n")

    # Fix ownership
    chown_recursive(home, uid, gid)

    log.info("Provisioned shell user: %s (uid=%d)", shell_username, uid)


async def ensure_shell_user(user_id: int, org_id: str, email: str, db: AsyncSession) -> tuple[str, str]:
    """Return (shell_username, org_id), provisioning if needed."""
    result = await db.execute(
        select(OrgMembership).where(
            OrgMembership.user_id == user_id,
            OrgMembership.org_id == org_id,
            OrgMembership.status == "active",
        )
    )
    membership = result.scalar_one()

    if membership.shell_username:
        return membership.shell_username, org_id

    username = generate_shell_username(email)

    # Check for collision
    existing = await db.execute(
        select(OrgMembership).where(OrgMembership.shell_username == username)
    )
    if existing.scalar() is not None:
        username = f"{username}-{user_id}"[:32]

    await asyncio.to_thread(provision_shell_user, org_id, username, user_id, email)

    # DuckLake auto-creates schemas on first use (e.g. when dbt runs CREATE SCHEMA)

    membership.shell_username = username
    await db.commit()
    return username, org_id
