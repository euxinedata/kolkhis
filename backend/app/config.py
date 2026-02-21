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
MAX_RESULT_ROWS = int(os.environ.get("MAX_RESULT_ROWS", "100000"))
RESULTS_PAGE_SIZE = int(os.environ.get("RESULTS_PAGE_SIZE", "100"))
