from functools import lru_cache

from pyiceberg.catalog.rest import RestCatalog

from app.config import LAKEKEEPER_URL, S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY, S3_REGION


@lru_cache(maxsize=128)
def get_database_catalog(lakekeeper_warehouse: str) -> RestCatalog:
    """Return a PyIceberg RestCatalog for a specific Lakekeeper warehouse (database)."""
    return RestCatalog(
        f"db-{lakekeeper_warehouse}",
        uri=f"{LAKEKEEPER_URL}/catalog",
        warehouse=lakekeeper_warehouse,
        **{
            "s3.endpoint": S3_ENDPOINT,
            "s3.access-key-id": S3_ACCESS_KEY,
            "s3.secret-access-key": S3_SECRET_KEY,
            "s3.region": S3_REGION,
        },
    )
