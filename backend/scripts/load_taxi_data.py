"""Load NYC yellow taxi trip data into the Iceberg catalog."""
import sys
from pathlib import Path

# Add backend to path so we can import app modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pyarrow.parquet as pq
from app.warehouse import catalog

PARQUET_FILE = "/tmp/kolkhis-data/yellow_tripdata_2024-01.parquet"
NAMESPACE = "nyc"
TABLE_NAME = "yellow_trips"


def main():
    # Read the downloaded parquet file
    print(f"Reading {PARQUET_FILE}...")
    table = pq.read_table(PARQUET_FILE)
    print(f"  {table.num_rows} rows, {table.num_columns} columns")
    print(f"  Schema: {table.schema}")

    # Create namespace if it doesn't exist
    existing_ns = [ns[0] for ns in catalog.list_namespaces()]
    if NAMESPACE not in existing_ns:
        print(f"Creating namespace '{NAMESPACE}'...")
        catalog.create_namespace(NAMESPACE)
    else:
        print(f"Namespace '{NAMESPACE}' already exists.")

    # Create or replace the table
    full_name = f"{NAMESPACE}.{TABLE_NAME}"
    existing_tables = [t[1] for t in catalog.list_tables(NAMESPACE)]
    if TABLE_NAME in existing_tables:
        print(f"Dropping existing table '{full_name}'...")
        catalog.drop_table(full_name)

    print(f"Creating table '{full_name}'...")
    iceberg_table = catalog.create_table(full_name, schema=table.schema)

    # Append the data
    print("Loading data...")
    iceberg_table.append(table)

    # Verify
    loaded = catalog.load_table(full_name)
    scan = loaded.scan()
    result = scan.to_arrow()
    print(f"Done. {result.num_rows} rows loaded into {full_name}.")


if __name__ == "__main__":
    main()
