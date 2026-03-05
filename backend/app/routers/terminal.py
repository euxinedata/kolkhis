import asyncio
import json
import logging

import asyncssh
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.auth import verify_token
from app.config import SHELL_MODE, SHELL_SSH_HOST, SHELL_SSH_PORT, SHELL_SSH_KEY_PATH
from app.database import async_session
from app.models import Organization
from app.shell import ensure_shell_user
from app.workspace import is_clone_ready

router = APIRouter()
log = logging.getLogger(__name__)


def _verify_ws_token(websocket: WebSocket) -> dict | None:
    """Extract and verify JWT from WebSocket cookies."""
    tok = websocket.cookies.get("token")
    if not tok:
        return None
    import jwt as pyjwt
    from app.config import JWT_SECRET
    try:
        return pyjwt.decode(tok, JWT_SECRET, algorithms=["HS256"])
    except pyjwt.InvalidTokenError:
        return None


WAREHOUSE_REPO = "warehouse"


@router.websocket("/api/terminal")
async def terminal_ws(websocket: WebSocket):
    # Authenticate
    payload = _verify_ws_token(websocket)
    if payload is None:
        await websocket.close(code=4401, reason="Not authenticated")
        return

    user_id = int(payload["sub"])
    user_email = payload.get("email", "")
    org_id = payload.get("org_id")

    if not org_id:
        await websocket.close(code=4403, reason="No organization selected")
        return

    # Look up shell username and resolve SSH host
    ssh_host = SHELL_SSH_HOST
    ssh_port = SHELL_SSH_PORT
    async with async_session() as session:
        shell_username, _ = await ensure_shell_user(user_id, org_id, user_email, session)
        if SHELL_MODE == "k8s":
            from app.shell_k8s import shell_host_for_org
            org = await session.get(Organization, org_id)
            if org:
                ssh_host = shell_host_for_org(org)
                ssh_port = 22

    if not is_clone_ready(org_id, shell_username, WAREHOUSE_REPO):
        await websocket.close(code=4503, reason="Workspace is being prepared")
        return

    await websocket.accept()

    conn = None
    try:
        # Connect to shell pod as the user's own account
        conn = await asyncssh.connect(
            ssh_host,
            port=ssh_port,
            username=shell_username,
            client_keys=[SHELL_SSH_KEY_PATH],
            known_hosts=None,
        )

        process = await conn.create_process(
            term_type="xterm-256color",
            term_size=(80, 24),
        )

        # cd into the warehouse repo directory
        process.stdin.write(f"cd ~/projects/{WAREHOUSE_REPO}\n")

        # Task: SSH stdout → WebSocket
        async def ssh_to_ws():
            try:
                while True:
                    data = await process.stdout.read(4096)
                    if not data:
                        break
                    await websocket.send_bytes(data.encode() if isinstance(data, str) else data)
            except (asyncssh.misc.DisconnectError, WebSocketDisconnect):
                pass

        # Task: WebSocket → SSH stdin
        async def ws_to_ssh():
            try:
                while True:
                    msg = await websocket.receive()
                    if msg.get("type") == "websocket.disconnect":
                        break
                    if "bytes" in msg and msg["bytes"]:
                        process.stdin.write(msg["bytes"].decode())
                    elif "text" in msg and msg["text"]:
                        try:
                            data = json.loads(msg["text"])
                            if data.get("type") == "resize":
                                process.change_terminal_size(
                                    data.get("cols", 80),
                                    data.get("rows", 24),
                                )
                        except (json.JSONDecodeError, KeyError):
                            pass
            except (WebSocketDisconnect, asyncssh.misc.DisconnectError):
                pass

        await asyncio.gather(ssh_to_ws(), ws_to_ssh(), return_exceptions=True)

    except asyncssh.misc.DisconnectError as e:
        log.warning("SSH disconnected: %s", e)
        try:
            await websocket.close(code=4502, reason="SSH connection failed")
        except Exception:
            pass
    except Exception as e:
        log.error("Terminal error: %s", e)
        try:
            await websocket.close(code=4500, reason="Internal error")
        except Exception:
            pass
    finally:
        if conn:
            conn.close()
