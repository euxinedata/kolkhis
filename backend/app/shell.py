"""Shell container user provisioning."""

import logging
import re
from pathlib import Path

import asyncssh
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import SHELL_SSH_HOST, SHELL_SSH_PORT, SHELL_SSH_USER, SHELL_SSH_KEY_PATH, HOMES_PATH
from app.models import User

log = logging.getLogger(__name__)


def generate_shell_username(email: str) -> str:
    """Derive a Linux username from an email address."""
    local = email.split("@")[0].lower()
    # Replace non-alphanumeric with dashes, collapse runs, strip edges
    username = re.sub(r"[^a-z0-9]+", "-", local).strip("-")
    return username[:32] if username else "user"


async def _run_ssh_command(command: str) -> str:
    """Run a command on the shell container as shelluser (admin)."""
    async with asyncssh.connect(
        SHELL_SSH_HOST,
        port=SHELL_SSH_PORT,
        username=SHELL_SSH_USER,
        client_keys=[SHELL_SSH_KEY_PATH],
        known_hosts=None,
    ) as conn:
        result = await conn.run(command)
        return result.stdout or ""


async def provision_shell_user(shell_username: str) -> None:
    """Create a Linux user in the shell container with home dir and SSH key."""
    # Create user (skip if already exists)
    await _run_ssh_command(
        f"id {shell_username} >/dev/null 2>&1 || "
        f"sudo useradd -m -s /bin/bash {shell_username}"
    )
    # Create standard directories and copy skel files (bashrc, profile)
    await _run_ssh_command(
        f"sudo mkdir -p /home/{shell_username}/.ssh "
        f"/home/{shell_username}/.dbt "
        f"/home/{shell_username}/projects"
    )
    await _run_ssh_command(
        f"sudo cp -n /etc/skel/.bashrc /etc/skel/.profile /home/{shell_username}/ 2>/dev/null; true"
    )
    # Copy the backend's public key so backend can SSH as this user
    await _run_ssh_command(
        f"sudo cp /home/shelluser/.ssh/authorized_keys "
        f"/home/{shell_username}/.ssh/authorized_keys"
    )
    # Seed ~/.dbt/profiles.yml with a comment header on host filesystem
    profiles_path = Path(HOMES_PATH) / shell_username / ".dbt" / "profiles.yml"
    profiles_path.parent.mkdir(parents=True, exist_ok=True)
    profiles_path.write_text("# dbt profiles — managed by Kolkhis\n")

    # Fix ownership
    await _run_ssh_command(
        f"sudo chown -R {shell_username}:{shell_username} /home/{shell_username}"
    )
    log.info("Provisioned shell user: %s", shell_username)


async def ensure_shell_user(user_id: int, email: str, db: AsyncSession) -> str:
    """Return the user's shell_username, provisioning if needed."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one()

    if user.shell_username:
        return user.shell_username

    username = generate_shell_username(email)

    # Check for collision
    existing = await db.execute(
        select(User).where(User.shell_username == username)
    )
    if existing.scalar() is not None:
        username = f"{username}-{user_id}"[:32]

    await provision_shell_user(username)

    user.shell_username = username
    await db.commit()
    return username
