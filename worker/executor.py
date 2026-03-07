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


def setup_ducklake_catalog(
    conn: duckdb.DuckDBPyConnection,
    pg_connection_string: str,
    databases: list[dict],
    s3_endpoint: str,
    s3_access_key: str,
    s3_secret_key: str,
    s3_region: str,
) -> None:
    """ATTACH DuckLake databases — one per org database.

    Each entry in ``databases`` has:
      - ``name``: logical DB name (e.g. "development")
      - ``data_path``: S3 path for Parquet data (e.g. "s3://org-id/development/")
      - ``metadata_schema``: PostgreSQL schema for DuckLake metadata
    """
    conn.install_extension("ducklake")
    conn.load_extension("ducklake")

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

    for db in databases:
        db_name = db["name"]
        data_path = db["data_path"]
        metadata_schema = db["metadata_schema"]
        # Append unique application_name to avoid DuckDB file handle deduplication
        unique_pg = f"{pg_connection_string} application_name=ducklake_{db_name}"
        conn.execute(f"""
            ATTACH 'ducklake:postgres:{unique_pg}'
              AS "{db_name}"
              (DATA_PATH '{data_path}',
               METADATA_SCHEMA '{metadata_schema}')
        """)
        logger.info("Attached DuckLake database: %s (schema=%s)", db_name, metadata_schema)


def _classify_sql(sql: str) -> str:
    """Classify SQL as 'query' (can be wrapped in CTAS) or 'command' (execute directly)."""
    normalized = sql.strip().upper()
    if normalized.startswith(("SELECT", "WITH", "VALUES", "FROM")):
        return "query"
    return "command"


def execute_query(
    job_id: str,
    sql: str,
    pg_connection_string: str,
    databases: list[dict],
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
        setup_ducklake_catalog(
            conn, pg_connection_string, databases,
            s3_endpoint, s3_access_key, s3_secret_key, s3_region,
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
