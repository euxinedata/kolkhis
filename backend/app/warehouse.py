"""DuckLake helpers — build connection strings and metadata schema names."""

from app.config import S3_BUCKET_NAME


def ducklake_metadata_schema(org_id: str, db_name: str) -> str:
    """Return the PostgreSQL schema name for DuckLake metadata.

    Uses first 8 chars of org_id (before first hyphen) to keep names short.
    """
    short_org = org_id.split("-")[0]
    return f"ducklake_{short_org}_{db_name}"


def ducklake_data_path(org_id: str, db_name: str) -> str:
    """Return the S3 data path for a DuckLake database."""
    return f"s3://{S3_BUCKET_NAME}/{org_id}/{db_name}/"
