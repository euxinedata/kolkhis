from functools import lru_cache

from pyiceberg.catalog.sql import SqlCatalog

from app.config import (
    DATABASE_URL_PLAIN,
    S3_ACCESS_KEY,
    S3_ENDPOINT,
    S3_REGION,
    S3_SECRET_KEY,
)


@lru_cache(maxsize=32)
def get_org_catalog(org_id: str) -> SqlCatalog:
    """Return a PyIceberg SqlCatalog scoped to an org's S3 bucket."""
    return SqlCatalog(
        f"org-{org_id}",
        uri=DATABASE_URL_PLAIN,
        warehouse=f"s3://{org_id}/warehouse",
        **{
            "s3.endpoint": S3_ENDPOINT,
            "s3.access-key-id": S3_ACCESS_KEY,
            "s3.secret-access-key": S3_SECRET_KEY,
            "s3.region": S3_REGION,
        },
    )
