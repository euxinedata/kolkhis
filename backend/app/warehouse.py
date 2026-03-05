from functools import lru_cache

from pyiceberg.catalog.rest import RestCatalog

from app.config import LAKEKEEPER_URL, S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY, S3_REGION


@lru_cache(maxsize=32)
def get_org_catalog(org_id: str) -> RestCatalog:
    """Return a PyIceberg RestCatalog backed by Lakekeeper."""
    return RestCatalog(
        f"org-{org_id}",
        uri=f"{LAKEKEEPER_URL}/catalog",
        warehouse="warehouse",
        **{
            "s3.endpoint": S3_ENDPOINT,
            "s3.access-key-id": S3_ACCESS_KEY,
            "s3.secret-access-key": S3_SECRET_KEY,
            "s3.region": S3_REGION,
        },
    )
