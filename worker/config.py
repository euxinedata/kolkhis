import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    # Load .env from project root (one level up from worker/)
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(_env_path)
except ModuleNotFoundError:
    pass  # dotenv not needed when env vars come from systemd EnvironmentFile

WORKER_AUTH_TOKEN: str = os.environ["WORKER_AUTH_TOKEN"]
S3_ENDPOINT: str = os.environ.get("S3_ENDPOINT", "")
S3_ACCESS_KEY: str = os.environ.get("S3_ACCESS_KEY", "")
S3_SECRET_KEY: str = os.environ.get("S3_SECRET_KEY", "")
S3_REGION: str = os.environ.get("S3_REGION", "us-east-1")
WORKER_PORT: int = int(os.environ.get("WORKER_PORT", "8080"))
