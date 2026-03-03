import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (two levels up from this file: app/ -> backend/ -> project root)
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
load_dotenv(_env_path)

GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"]
JWT_SECRET = os.environ["JWT_SECRET"]
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173")

POSTGRES_USER = os.environ.get("POSTGRES_USER", "euxine")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD", "very_secure_password")
POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.environ.get("POSTGRES_PORT", "5437")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "euxine")

DATABASE_URL_ASYNC = (
    f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)
DATABASE_URL_SYNC = (
    f"postgresql+psycopg2://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)

# PyIceberg needs a plain postgresql:// URI (no driver suffix)
DATABASE_URL_PLAIN = (
    f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)

WAREHOUSE_PATH = os.environ.get("WAREHOUSE_PATH", "/mnt/warehouse")
RESULTS_PATH = os.environ.get("RESULTS_PATH", "/tmp/warehouse-results")

# S3-compatible object storage (used when WAREHOUSE_PATH starts with "s3://")
S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "http://localhost:9000")
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "minioadmin")
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "minioadmin")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")


def is_s3_warehouse() -> bool:
    return WAREHOUSE_PATH.startswith("s3://")


MAX_RESULT_ROWS = int(os.environ.get("MAX_RESULT_ROWS", "100000"))
RESULTS_PAGE_SIZE = int(os.environ.get("RESULTS_PAGE_SIZE", "100"))

# Worker VM configuration
WORKER_MODE = os.environ.get("WORKER_MODE", "local")  # "local", "local-worker", or "remote"
WORKER_URL = os.environ.get("WORKER_URL", "http://localhost:8080")
WORKER_AUTH_TOKEN = os.environ.get("WORKER_AUTH_TOKEN", "")
WORKER_SNAPSHOT_ID = os.environ.get("WORKER_SNAPSHOT_ID", "")
WORKER_SERVER_TYPE = os.environ.get("WORKER_SERVER_TYPE", "cpx21")
WORKER_LOCATION = os.environ.get("WORKER_LOCATION", "fsn1")
WORKER_NETWORK_ID = int(os.environ.get("WORKER_NETWORK_ID", "0"))
HCLOUD_TOKEN = os.environ.get("HCLOUD_TOKEN", "")
WORKER_IDLE_TIMEOUT = int(os.environ.get("WORKER_IDLE_TIMEOUT", "900"))  # 15 min
S3_RESULTS_BUCKET = os.environ.get("S3_RESULTS_BUCKET", "")
# Separate S3 config for results (defaults to warehouse S3 if not set)
S3_RESULTS_ENDPOINT = os.environ.get("S3_RESULTS_ENDPOINT", S3_ENDPOINT)
S3_RESULTS_ACCESS_KEY = os.environ.get("S3_RESULTS_ACCESS_KEY", S3_ACCESS_KEY)
S3_RESULTS_SECRET_KEY = os.environ.get("S3_RESULTS_SECRET_KEY", S3_SECRET_KEY)
S3_RESULTS_REGION = os.environ.get("S3_RESULTS_REGION", S3_REGION)

# Gitea configuration
GITEA_URL = os.environ.get("GITEA_URL", "http://gitea:3000")
GITEA_SHELL_URL = os.environ.get("GITEA_SHELL_URL", GITEA_URL)
GITEA_ADMIN_USER = os.environ.get("GITEA_ADMIN_USER", "kolkhis-admin")
GITEA_ADMIN_PASSWORD = os.environ.get("GITEA_ADMIN_PASSWORD", "")
HOMES_PATH = os.environ.get("HOMES_PATH", "./data/homes")

# Shell pod SSH configuration
SHELL_SSH_HOST = os.environ.get("SHELL_SSH_HOST", "localhost")
SHELL_SSH_PORT = int(os.environ.get("SHELL_SSH_PORT", "2222"))
SHELL_SSH_USER = os.environ.get("SHELL_SSH_USER", "shelluser")
SHELL_SSH_KEY_PATH = os.environ.get("SHELL_SSH_KEY_PATH", str(Path(__file__).resolve().parent.parent.parent / "shell" / "keys" / "id_ed25519"))
