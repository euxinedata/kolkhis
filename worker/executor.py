import os
import re
import shutil
import tempfile

import duckdb
import pyarrow.parquet as pq


# Module-level dict for cancellation support: job_id -> duckdb.DuckDBPyConnection
_running_conns: dict[str, duckdb.DuckDBPyConnection] = {}


def cancel(job_id: str) -> bool:
    conn = _running_conns.get(job_id)
    if conn is None:
        return False
    conn.interrupt()
    return True


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
    _running_conns[job_id] = conn

    try:
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

        created_schemas: set[str] = set()
        sorted_objects = sorted(
            catalog_objects, key=lambda o: 0 if o["object_type"] == "table" else 1
        )

        for obj in sorted_objects:
            schema = obj["duckdb_schema"]
            if schema not in created_schemas:
                conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
                created_schemas.add(schema)

            if obj["object_type"] == "table" and obj.get("metadata_location"):
                conn.execute(
                    f'CREATE VIEW "{schema}"."{obj["name"]}" AS '
                    f"SELECT * FROM iceberg_scan('{obj['metadata_location']}')"
                )
            elif obj["object_type"] == "view" and obj.get("view_sql"):
                try:
                    conn.execute(
                        f'CREATE VIEW "{schema}"."{obj["name"]}" AS {obj["view_sql"]}'
                    )
                except duckdb.Error:
                    pass

        sql = sql.strip().rstrip(";").strip()
        if not re.search(
            r"\bLIMIT\s+\d+(\s+OFFSET\s+\d+)?\s*$", sql, re.IGNORECASE
        ):
            sql = f"{sql} LIMIT {max_result_rows}"

        result = conn.execute(sql)
        arrow_table = result.fetch_arrow_table()
        row_count = arrow_table.num_rows

        pq.write_table(arrow_table, result_path)

        return row_count
    finally:
        _running_conns.pop(job_id, None)
        conn.close()
        shutil.rmtree(temp_dir, ignore_errors=True)
