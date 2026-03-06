import logging
import os
import re
import shutil
import tempfile

import duckdb

logger = logging.getLogger(__name__)


# Module-level dict for cancellation support: job_id -> duckdb.DuckDBPyConnection
_running_conns: dict[str, duckdb.DuckDBPyConnection] = {}


def cancel(job_id: str) -> bool:
    conn = _running_conns.get(job_id)
    if conn is None:
        return False
    conn.interrupt()
    return True


def _setup_overlay(
    conn: duckdb.DuckDBPyConnection,
    db_name: str,
    ice_name: str,
    views: list[dict],
) -> None:
    """Create a memory overlay for an Iceberg database to support views.

    Enumerates schemas/tables from the Iceberg-ATTACHed database (ice_name),
    creates matching schemas in a :memory: database (db_name), creates
    pass-through views for all tables, then registers user-defined views.
    """
    conn.execute(f"""ATTACH ':memory:' AS "{db_name}" """)

    schemas = conn.execute(
        f"SELECT DISTINCT schema_name FROM duckdb_schemas() WHERE database_name = '{ice_name}'"
    ).fetchall()

    for (schema_name,) in schemas:
        conn.execute(f'CREATE SCHEMA "{db_name}"."{schema_name}"')
        tables = conn.execute(
            f"SELECT table_name FROM duckdb_tables() "
            f"WHERE database_name = '{ice_name}' AND schema_name = '{schema_name}'"
        ).fetchall()
        for (table_name,) in tables:
            conn.execute(
                f'CREATE VIEW "{db_name}"."{schema_name}"."{table_name}" AS '
                f'SELECT * FROM "{ice_name}"."{schema_name}"."{table_name}"'
            )

    for v in views:
        schema_key = f'"{db_name}"."{v["schema_name"]}"'
        conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_key}")
        try:
            conn.execute(
                f'CREATE OR REPLACE VIEW {schema_key}."{v["name"]}" AS {v["view_sql"]}'
            )
        except duckdb.Error as e:
            logger.warning("Failed to register view %s.%s.%s: %s", db_name, v["schema_name"], v["name"], e)


def setup_iceberg_catalog(
    conn: duckdb.DuckDBPyConnection,
    lakekeeper_url: str,
    databases: list[dict],
    s3_endpoint: str,
    s3_access_key: str,
    s3_secret_key: str,
    s3_region: str,
    views: list[dict] | None = None,
    force_overlay: bool = False,
) -> None:
    """ATTACH Iceberg REST catalogs — one per database.

    Each entry in ``databases`` has ``name`` (logical DB name, e.g. "development")
    and ``lakekeeper_warehouse`` (the Lakekeeper warehouse identifier).

    When views exist for a database (or force_overlay is True), uses a memory
    overlay: Iceberg is ATTACHed under a ``_ice_`` prefix and a :memory: database
    takes the user-facing name, with pass-through views for all tables.
    """
    conn.install_extension("iceberg")
    conn.load_extension("iceberg")
    conn.install_extension("httpfs")
    conn.load_extension("httpfs")

    use_ssl = "true" if s3_endpoint.startswith("https://") else "false"
    bare_endpoint = s3_endpoint.replace("http://", "").replace("https://", "")
    conn.execute(f"""
        CREATE SECRET (
            TYPE S3,
            KEY_ID '{s3_access_key}',
            SECRET '{s3_secret_key}',
            REGION '{s3_region}',
            ENDPOINT '{bare_endpoint}',
            URL_STYLE 'path',
            USE_SSL {use_ssl}
        )
    """)

    views = views or []
    views_by_db: dict[str, list[dict]] = {}
    for v in views:
        views_by_db.setdefault(v["database"], []).append(v)

    catalog_endpoint = f"{lakekeeper_url}/catalog"
    for db in databases:
        db_name = db["name"]
        needs_overlay = force_overlay or db_name in views_by_db

        if needs_overlay:
            ice_name = f"_ice_{db_name}"
            conn.execute(f"""
                ATTACH '{db["lakekeeper_warehouse"]}' AS "{ice_name}" (
                    TYPE ICEBERG,
                    ENDPOINT '{catalog_endpoint}',
                    AUTHORIZATION_TYPE 'none',
                    ACCESS_DELEGATION_MODE 'none'
                )
            """)
            _setup_overlay(conn, db_name, ice_name, views_by_db.get(db_name, []))
            logger.info("Attached database with overlay: %s -> %s", db_name, db["lakekeeper_warehouse"])
        else:
            conn.execute(f"""
                ATTACH '{db["lakekeeper_warehouse"]}' AS "{db_name}" (
                    TYPE ICEBERG,
                    ENDPOINT '{catalog_endpoint}',
                    AUTHORIZATION_TYPE 'none',
                    ACCESS_DELEGATION_MODE 'none'
                )
            """)
            logger.info("Attached database: %s -> %s", db_name, db["lakekeeper_warehouse"])


def setup_connection(
    conn: duckdb.DuckDBPyConnection,
    catalog_objects: list[dict],
    s3_endpoint: str,
    s3_access_key: str,
    s3_secret_key: str,
    s3_region: str,
) -> None:
    """Load extensions, configure S3, and register catalog objects on a DuckDB connection."""
    conn.install_extension("iceberg")
    conn.load_extension("iceberg")
    conn.install_extension("httpfs")
    conn.load_extension("httpfs")

    use_ssl = "true" if s3_endpoint.startswith("https://") else "false"
    bare_endpoint = s3_endpoint.replace("http://", "").replace("https://", "")
    conn.execute(f"""
        CREATE SECRET (
            TYPE S3,
            KEY_ID '{s3_access_key}',
            SECRET '{s3_secret_key}',
            REGION '{s3_region}',
            ENDPOINT '{bare_endpoint}',
            URL_STYLE 'path',
            USE_SSL {use_ssl}
        )
    """)

    created_dbs: set[str] = set()
    created_schemas: set[str] = set()
    sorted_objects = sorted(
        catalog_objects, key=lambda o: 0 if o["object_type"] == "table" else 1
    )

    for obj in sorted_objects:
        parts = obj["duckdb_schema"].split(".", 1)
        db_name, schema_name = parts[0], parts[1] if len(parts) > 1 else "main"

        if db_name not in created_dbs:
            conn.execute(f"ATTACH ':memory:' AS \"{db_name}\"")
            created_dbs.add(db_name)

        full_schema = f'"{db_name}"."{schema_name}"'
        if full_schema not in created_schemas:
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {full_schema}")
            created_schemas.add(full_schema)

        qualified_name = f'{full_schema}."{obj["name"]}"'

        if obj["object_type"] == "table" and obj.get("metadata_location"):
            conn.execute(
                f"CREATE VIEW {qualified_name} AS "
                f"SELECT * FROM iceberg_scan('{obj['metadata_location']}')"
            )
        elif obj["object_type"] == "view" and obj.get("view_sql"):
            try:
                conn.execute(
                    f"CREATE VIEW {qualified_name} AS {obj['view_sql']}"
                )
            except duckdb.Error:
                pass


def _classify_sql(sql: str) -> str:
    """Classify SQL as 'query' (can be wrapped in CTAS) or 'command' (execute directly).

    Only statements that produce a table-like result and can be wrapped in
    CREATE TEMP TABLE _results AS ... are classified as 'query'.
    DESCRIBE, EXPLAIN, PRAGMA, and SHOW return results but cannot be wrapped in CTAS.
    """
    normalized = sql.strip().upper()
    if normalized.startswith(("SELECT", "WITH", "VALUES", "FROM")):
        return "query"
    return "command"


def execute_query(
    job_id: str,
    sql: str,
    lakekeeper_url: str,
    databases: list[dict],
    s3_endpoint: str,
    s3_access_key: str,
    s3_secret_key: str,
    s3_region: str,
    result_path: str,
    max_result_rows: int,
    views: list[dict] | None = None,
) -> int:
    temp_dir = os.path.join(tempfile.gettempdir(), f".tmp_{job_id}")
    os.makedirs(temp_dir, exist_ok=True)

    conn = duckdb.connect()
    conn.execute(f"SET temp_directory='{temp_dir}'")
    if os.path.isdir("/opt/kolkhis-worker"):
        conn.execute("SET home_directory='/opt/kolkhis-worker'")
    _running_conns[job_id] = conn

    try:
        setup_iceberg_catalog(
            conn, lakekeeper_url, databases,
            s3_endpoint, s3_access_key, s3_secret_key, s3_region,
            views=views,
        )

        sql = sql.strip().rstrip(";").strip()

        if _classify_sql(sql) == "query":
            if not re.search(
                r"\bLIMIT\s+\d+(\s+OFFSET\s+\d+)?\s*$", sql, re.IGNORECASE
            ):
                sql = f"{sql} LIMIT {max_result_rows}"

            conn.execute(f"CREATE TEMP TABLE _results AS {sql}")
            row_count = conn.execute("SELECT count(*) FROM _results").fetchone()[0]
            conn.execute(f"COPY _results TO '{result_path}' (FORMAT PARQUET)")
        else:
            result = conn.execute(sql)
            rows_affected = -1
            if result.description:
                row = result.fetchone()
                if row is not None and isinstance(row[0], (int, float)):
                    rows_affected = int(row[0])
            conn.execute(
                f"COPY (SELECT 'OK' AS status, {rows_affected} AS rows_affected) "
                f"TO '{result_path}' (FORMAT PARQUET)"
            )
            row_count = 1

        return row_count
    finally:
        _running_conns.pop(job_id, None)
        conn.close()
        shutil.rmtree(temp_dir, ignore_errors=True)
