import asyncio

from fastapi import APIRouter, Depends, Header, HTTPException

from app.config import (
    S3_ACCESS_KEY,
    S3_ENDPOINT,
    S3_REGION,
    S3_SECRET_KEY,
    WORKER_AUTH_TOKEN,
)
from app.query_engine import _load_catalog_objects
from app.warehouse import catalog

router = APIRouter(prefix="/api/dbt")


def _verify_token(authorization: str = Header()) -> None:
    """Verify bearer token matches the shared worker auth token."""
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    if authorization[len(prefix):] != WORKER_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid token")


@router.get("/session-config")
async def session_config(_: None = Depends(_verify_token)):
    """Return catalog objects with resolved metadata locations and S3 config.

    Used by the dbt-kolkhis adapter to create a worker session.
    """
    catalog_objects = await _load_catalog_objects()

    resolved = []
    for obj in catalog_objects:
        entry = {
            "duckdb_schema": f"{obj['database']}.{obj['schema']}",
            "name": obj["name"],
            "object_type": obj["object_type"],
        }
        if obj["object_type"] == "table" and obj["iceberg_identifier"]:
            tbl = await asyncio.to_thread(
                catalog.load_table, obj["iceberg_identifier"]
            )
            entry["metadata_location"] = tbl.metadata_location
        elif obj["object_type"] == "view" and obj["view_sql"]:
            entry["view_sql"] = obj["view_sql"]
        resolved.append(entry)

    return {
        "catalog_objects": resolved,
        "s3": {
            "endpoint": S3_ENDPOINT,
            "access_key": S3_ACCESS_KEY,
            "secret_key": S3_SECRET_KEY,
            "region": S3_REGION,
        },
    }
