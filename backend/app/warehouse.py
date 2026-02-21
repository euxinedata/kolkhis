from pyiceberg.catalog.sql import SqlCatalog

from app.config import DATABASE_URL_PLAIN, WAREHOUSE_PATH

catalog = SqlCatalog(
    "kolkhis",
    **{"uri": DATABASE_URL_PLAIN, "warehouse": WAREHOUSE_PATH},
)
