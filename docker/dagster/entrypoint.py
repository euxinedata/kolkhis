"""
dagster-code entrypoint: HTTP reload API + Dagster gRPC lifecycle.

Endpoints:
  POST /reload  {"org_id": "...", "repo": "warehouse"}
    -> git clone/pull from Gitea, (re)start gRPC if dagster/definitions.py exists
  GET  /status
    -> {"loaded": bool, "org_id": str|null, "commit": str|null, "grpc_running": bool}
"""

import json
import logging
import os
import signal
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("entrypoint")

REPOS_DIR = Path("/opt/dagster/repos")
GITEA_URL = os.environ.get("GITEA_SHELL_URL", "http://gitea:3000")
GITEA_USER = os.environ.get("GITEA_ADMIN_USER", "")
GITEA_PASS = os.environ.get("GITEA_ADMIN_PASSWORD", "")

# State
_lock = threading.Lock()
_grpc_proc: subprocess.Popen | None = None
_current_org: str | None = None
_current_commit: str | None = None


def _git_clone_or_pull(org_id: str, repo: str) -> Path:
    """Clone or pull the repo. Returns the repo path."""
    repo_dir = REPOS_DIR / org_id / repo
    auth_url = GITEA_URL.replace("://", f"://{GITEA_USER}:{GITEA_PASS}@")
    remote = f"{auth_url}/{org_id}/{repo}.git"

    if (repo_dir / ".git").exists():
        log.info("Pulling %s/%s", org_id, repo)
        subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=repo_dir, capture_output=True, text=True, check=True,
        )
    else:
        log.info("Cloning %s/%s", org_id, repo)
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", remote, str(repo_dir)],
            capture_output=True, text=True, check=True,
        )
    return repo_dir


def _get_commit(repo_dir: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_dir, capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except Exception:
        return None


def _stop_grpc() -> None:
    global _grpc_proc
    if _grpc_proc and _grpc_proc.poll() is None:
        log.info("Stopping gRPC server (pid %d)", _grpc_proc.pid)
        _grpc_proc.terminate()
        try:
            _grpc_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _grpc_proc.kill()
            _grpc_proc.wait()
    _grpc_proc = None


def _start_grpc(repo_dir: Path) -> bool:
    global _grpc_proc
    definitions_file = repo_dir / "dagster" / "definitions.py"
    if not definitions_file.exists():
        log.info("No dagster/definitions.py found -- gRPC not started")
        return False

    _stop_grpc()
    dagster_dir = repo_dir / "dagster"
    log.info("Starting gRPC server from %s", dagster_dir)
    _grpc_proc = subprocess.Popen(
        [
            "dagster", "api", "grpc",
            "-h", "0.0.0.0",
            "-p", "3030",
            "-f", str(definitions_file),
        ],
        cwd=str(dagster_dir),
    )
    log.info("gRPC server started (pid %d)", _grpc_proc.pid)
    return True


def _do_reload(org_id: str, repo: str) -> dict:
    global _current_org, _current_commit
    with _lock:
        repo_dir = _git_clone_or_pull(org_id, repo)
        _current_org = org_id
        _current_commit = _get_commit(repo_dir)
        loaded = _start_grpc(repo_dir)
        return {
            "loaded": loaded,
            "org_id": org_id,
            "commit": _current_commit,
            "grpc_running": loaded,
        }


def _get_status() -> dict:
    with _lock:
        grpc_running = _grpc_proc is not None and _grpc_proc.poll() is None
        return {
            "loaded": grpc_running,
            "org_id": _current_org,
            "commit": _current_commit,
            "grpc_running": grpc_running,
        }


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/reload":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length)) if length else {}
                org_id = body.get("org_id")
                repo = body.get("repo", "warehouse")
                if not org_id:
                    self._json(400, {"error": "org_id required"})
                    return
                result = _do_reload(org_id, repo)
                self._json(200, result)
            except subprocess.CalledProcessError as e:
                log.error("Git operation failed: %s", e.stderr)
                self._json(500, {"error": "git operation failed", "detail": str(e.stderr)})
            except Exception as e:
                log.exception("Reload failed")
                self._json(500, {"error": str(e)})
        else:
            self._json(404, {"error": "not found"})

    def do_GET(self):
        if self.path == "/status":
            self._json(200, _get_status())
        else:
            self._json(404, {"error": "not found"})

    def _json(self, code: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002
        log.info(format, *args)


def main():
    REPOS_DIR.mkdir(parents=True, exist_ok=True)

    def _shutdown(signum, _frame):
        log.info("Shutting down (signal %d)", signum)
        _stop_grpc()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    server = HTTPServer(("0.0.0.0", 3031), Handler)
    log.info("Reload API listening on :3031")
    server.serve_forever()


if __name__ == "__main__":
    main()
