"""Load all NYC yellow taxi trip data into DuckLake tables.

Two tables due to schema change in July 2016:
  - nyc.yellow_trips_legacy (2009-01 through 2016-06): lat/lon columns
  - nyc.yellow_trips (2016-07 onwards): location ID columns

Creates a 'nyc_taxi' database in the org's DuckLake warehouse if it doesn't exist.

Usage:
    cd backend/
    python scripts/load_taxi_data.py <org_uuid>
"""
import glob
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import create_engine, text

from app.config import DATABASE_URL_SYNC
from scripts.generate_retail_data import (
    create_table_from_arrow,
    get_ducklake_conn,
    insert_arrow_table,
    insert_org_database,
)

DATA_DIR = "/tmp/kolkhis-data"
DB_NAME = "nyc_taxi"
NAMESPACE = "nyc"

# Schema cutover: 2016-07 switched from lat/lon to PULocationID/DOLocationID
CUTOVER = "2016-07"


def get_file_month(filepath: str) -> str:
    name = Path(filepath).stem
    return name.split("_")[-1]


def _find_canonical_schema(files: list[str]) -> pa.Schema:
    for f in reversed(files):
        schema = pq.read_schema(f)
        if not any(field.type == pa.null() for field in schema):
            return schema
    schema = pq.read_schema(files[-1])
    fields = []
    for field in schema:
        if field.type == pa.null():
            fields.append(pa.field(field.name, pa.float64(), nullable=True))
        else:
            fields.append(field)
    return pa.schema(fields)


def _align_table(table: pa.Table, target_schema: pa.Schema) -> pa.Table:
    if table.schema == target_schema:
        return table
    aligned_columns = []
    for field in target_schema:
        if field.name in table.column_names:
            col = table.column(field.name)
            if col.type == pa.null() or col.type != field.type:
                if col.type == pa.null():
                    col = pa.nulls(len(table), type=field.type)
                else:
                    col = col.cast(field.type)
            aligned_columns.append(col)
        else:
            aligned_columns.append(pa.nulls(len(table), type=field.type))
    return pa.table(aligned_columns, schema=target_schema)


def load_files(conn, files: list[str], table_name: str):
    if not files:
        print(f"No files for {table_name}, skipping")
        return

    files.sort()
    print(f"\nLoading {len(files)} files into {NAMESPACE}.{table_name}")

    canonical_schema = _find_canonical_schema(files)

    total_rows = 0
    for i, filepath in enumerate(files):
        month = get_file_month(filepath)
        try:
            table = pq.read_table(filepath)
            table = _align_table(table, canonical_schema)

            if i == 0:
                create_table_from_arrow(conn, DB_NAME, NAMESPACE, table_name, table)
            else:
                insert_arrow_table(conn, DB_NAME, NAMESPACE, table_name, table)
            total_rows += table.num_rows
            print(f"  [{i+1}/{len(files)}] {month}: {table.num_rows:,} rows")
        except Exception as e:
            print(f"  [{i+1}/{len(files)}] {month}: FAILED - {e}")

    print(f"  Total: {total_rows:,} rows in {NAMESPACE}.{table_name}")


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/load_taxi_data.py <org_uuid>")
        sys.exit(1)

    org_id = sys.argv[1]

    # Verify org exists
    engine = create_engine(DATABASE_URL_SYNC)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id FROM organizations WHERE id = :id"),
            {"id": org_id},
        ).fetchone()
        if not row:
            print(f"ERROR: Organization {org_id} not found")
            sys.exit(1)

    # Provision database
    print(f"Provisioning database: {DB_NAME}")
    insert_org_database(engine, org_id, DB_NAME)
    duck = get_ducklake_conn(org_id, DB_NAME)
    duck.execute(f'CREATE SCHEMA IF NOT EXISTS "{DB_NAME}"."{NAMESPACE}"')

    # Gather all valid parquet files
    all_files = sorted(glob.glob(f"{DATA_DIR}/yellow_tripdata_*.parquet"))
    valid_files = []
    for f in all_files:
        try:
            pq.read_schema(f)
            valid_files.append(f)
        except Exception:
            print(f"Skipping incomplete file: {Path(f).name}")

    legacy_files = [f for f in valid_files if get_file_month(f) < CUTOVER]
    modern_files = [f for f in valid_files if get_file_month(f) >= CUTOVER]

    print(f"Found {len(valid_files)} valid files: "
          f"{len(legacy_files)} legacy, {len(modern_files)} modern")

    # Check existing tables
    existing = set()
    try:
        result = duck.execute(
            f"SELECT table_name FROM information_schema.tables "
            f"WHERE table_catalog = '{DB_NAME}' AND table_schema = '{NAMESPACE}'"
        ).fetchall()
        existing = {r[0] for r in result}
    except Exception:
        pass

    if "yellow_trips_legacy" in existing:
        print(f"\nSkipping {NAMESPACE}.yellow_trips_legacy (already loaded)")
    else:
        load_files(duck, legacy_files, "yellow_trips_legacy")

    if "yellow_trips" in existing:
        print(f"Dropping existing table {NAMESPACE}.yellow_trips")
        duck.execute(f'DROP TABLE IF EXISTS "{DB_NAME}"."{NAMESPACE}"."yellow_trips"')
    load_files(duck, modern_files, "yellow_trips")

    duck.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
