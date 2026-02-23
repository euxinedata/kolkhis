from pyiceberg.catalog.sql import SqlCatalog

from app.config import (
    DATABASE_URL_PLAIN,
    S3_ACCESS_KEY,
    S3_ENDPOINT,
    S3_REGION,
    S3_SECRET_KEY,
    WAREHOUSE_PATH,
    is_s3_warehouse,
)

_catalog_props: dict[str, str] = {
    "uri": DATABASE_URL_PLAIN,
    "warehouse": WAREHOUSE_PATH,
}

if is_s3_warehouse():
    _catalog_props.update({
        "s3.endpoint": S3_ENDPOINT,
        "s3.access-key-id": S3_ACCESS_KEY,
        "s3.secret-access-key": S3_SECRET_KEY,
        "s3.region": S3_REGION,
    })

catalog = SqlCatalog("kolkhis", **_catalog_props)
