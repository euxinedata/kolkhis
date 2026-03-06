import logging
import os
import re
import shutil
import tempfile

import duckdb
import httpx

logger = logging.getLogger(__name__)


# Module-level dict for cancellation support: job_id -> duckdb.DuckDBPyConnection
_running_conns: dict[str, duckdb.DuckDBPyConnection] = {}


def cancel(job_id: str) -> bool:
    conn = _running_conns.get(job_id)
    if conn is None:
        return False
    conn.interrupt()
    return True


def _create_namespace_aliases(
    conn: duckdb.DuckDBPyConnection,
    catalog_endpoint: str,
    warehouse: str,
) -> None:
    """Create in-memory alias databases/schemas so nested Iceberg namespaces
    like retail.products can be queried as retail.products.table_name
    (database.schema.table), matching the SQL Query workbook convention."""
    # Get the catalog prefix from the REST config endpoint
    try:
        resp = httpx.get(f"{catalog_endpoint}/v1/config", params={"warehouse": warehouse}, timeout=10)
        resp.raise_for_status()
        prefix = resp.json().get("defaults", {}).get("prefix", "")
    except Exception as exc:
        logger.warning("Failed to get catalog config for aliases: %s", exc)
        return

    if not prefix:
        return

    # Enumerate all top-level namespaces
    try:
        resp = httpx.get(f"{catalog_endpoint}/v1/{prefix}/namespaces", timeout=10)
        resp.raise_for_status()
        top_namespaces = [ns[0] for ns in resp.json().get("namespaces", []) if len(ns) == 1]
    except Exception as exc:
        logger.warning("Failed to list namespaces: %s", exc)
        return

    created_dbs: set[str] = set()

    for top_ns in top_namespaces:
        # Check for nested namespaces under this one
        try:
            resp = httpx.get(
                f"{catalog_endpoint}/v1/{prefix}/namespaces",
                params={"parent": top_ns},
                timeout=10,
            )
            resp.raise_for_status()
            nested = resp.json().get("namespaces", [])
        except Exception:
            continue

        for ns_parts in nested:
            if len(ns_parts) < 2:
                continue
            db_name = ns_parts[0]
            schema_name = ns_parts[1]
            iceberg_schema = ".".join(ns_parts)  # e.g. "retail.products"

            # Create in-memory database for the top-level namespace
            if db_name not in created_dbs:
                try:
                    conn.execute(f'ATTACH \':memory:\' AS "{db_name}"')
                    created_dbs.add(db_name)
                    logger.info("Created alias database: %s", db_name)
                except duckdb.Error as exc:
                    logger.warning("Failed to create alias database %s: %s", db_name, exc)

            if db_name not in created_dbs:
                continue

            # Create schema
            try:
                conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{db_name}"."{schema_name}"')
            except duckdb.Error as exc:
                logger.warning("Failed to create alias schema %s.%s: %s", db_name, schema_name, exc)
                continue

            # List tables in this namespace via Lakekeeper REST API
            # Namespace separator in Iceberg REST is %1F (unit separator)
            ns_path = "%1F".join(ns_parts)
            try:
                resp = httpx.get(
                    f"{catalog_endpoint}/v1/{prefix}/namespaces/{ns_path}/tables",
                    timeout=10,
                )
                resp.raise_for_status()
                tables = resp.json().get("identifiers", [])
            except Exception as exc:
                logger.warning("Failed to list tables in %s: %s", iceberg_schema, exc)
                continue

            for tbl in tables:
                tbl_name = tbl["name"]
                try:
                    conn.execute(
                        f'CREATE VIEW "{db_name}"."{schema_name}"."{tbl_name}" AS '
                        f'SELECT * FROM {warehouse}."{iceberg_schema}"."{tbl_name}"'
                    )
                except duckdb.Error as exc:
                    logger.warning("Failed to create alias view %s.%s.%s: %s", db_name, schema_name, tbl_name, exc)

    logger.info("Namespace alias creation complete")


def setup_iceberg_catalog(
    conn: duckdb.DuckDBPyConnection,
    lakekeeper_url: str,
    warehouse: str,
    s3_endpoint: str,
    s3_access_key: str,
    s3_secret_key: str,
    s3_region: str,
) -> None:
    """ATTACH an Iceberg REST catalog for full DDL/DML support."""
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

    catalog_endpoint = f"{lakekeeper_url}/catalog"
    # Attach the Iceberg catalog as "warehouse" so dbt profiles.yml
    # can use database: warehouse regardless of the org UUID.
    conn.execute(f"""
        ATTACH '{warehouse}' AS warehouse (
            TYPE ICEBERG,
            ENDPOINT '{catalog_endpoint}',
            AUTHORIZATION_TYPE 'none',
            ACCESS_DELEGATION_MODE 'none'
        )
    """)

    # Create alias views so nested namespaces like retail.products.brands
    # work the same way as in the SQL Query workbook (database.schema.table).
    _create_namespace_aliases(conn, catalog_endpoint, "warehouse")


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

    # Always create the default 'kolkhis' database for dbt target writes
    conn.execute('ATTACH \':memory:\' AS "kolkhis"')
    conn.execute('CREATE SCHEMA IF NOT EXISTS "kolkhis"."main"')

    created_dbs: set[str] = {"kolkhis"}
    created_schemas: set[str] = {'"kolkhis"."main"'}
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


def execute_query(
    job_id: str,
    sql: str,
    catalog_objects: list[dict],
    s3_endpoint: str,
    s3_access_key: str,
    s3_secret_key: str,
    s3_region: str,
    result_path: str,
    max_result_rows: int,
) -> int:
    temp_dir = os.path.join(tempfile.gettempdir(), f".tmp_{job_id}")
    os.makedirs(temp_dir, exist_ok=True)

    conn = duckdb.connect()
    conn.execute(f"SET temp_directory='{temp_dir}'")
    if os.path.isdir("/opt/kolkhis-worker"):
        conn.execute("SET home_directory='/opt/kolkhis-worker'")
    _running_conns[job_id] = conn

    try:
        setup_connection(
            conn, catalog_objects,
            s3_endpoint, s3_access_key, s3_secret_key, s3_region,
        )

        sql = sql.strip().rstrip(";").strip()
        if not re.search(
            r"\bLIMIT\s+\d+(\s+OFFSET\s+\d+)?\s*$", sql, re.IGNORECASE
        ):
            sql = f"{sql} LIMIT {max_result_rows}"

        # Write results using DuckDB's COPY so it uses the configured S3 secret
        conn.execute(f"CREATE TEMP TABLE _results AS {sql}")
        row_count = conn.execute("SELECT count(*) FROM _results").fetchone()[0]
        conn.execute(f"COPY _results TO '{result_path}' (FORMAT PARQUET)")

        return row_count
    finally:
        _running_conns.pop(job_id, None)
        conn.close()
        shutil.rmtree(temp_dir, ignore_errors=True)
