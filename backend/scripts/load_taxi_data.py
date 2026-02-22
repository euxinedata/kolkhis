"""Load all NYC yellow taxi trip data into Iceberg tables.

Two tables due to schema change in July 2016:
  - nyc.yellow_trips_legacy (2009-01 through 2016-06): lat/lon columns
  - nyc.yellow_trips (2016-07 onwards): location ID columns
"""
import glob
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pyarrow as pa
import pyarrow.parquet as pq
from app.warehouse import catalog

DATA_DIR = "/tmp/kolkhis-data"
NAMESPACE = "nyc"

# Schema cutover: 2016-07 switched from lat/lon to PULocationID/DOLocationID
CUTOVER = "2016-07"


def ensure_namespace():
    existing = [ns[0] for ns in catalog.list_namespaces()]
    if NAMESPACE not in existing:
        print(f"Creating namespace '{NAMESPACE}'")
        catalog.create_namespace(NAMESPACE)


def create_or_get_table(table_name: str, schema: pa.Schema):
    full_name = f"{NAMESPACE}.{table_name}"
    existing = [t[1] for t in catalog.list_tables(NAMESPACE)]
    if table_name in existing:
        print(f"  Table '{full_name}' exists, will append")
        return catalog.load_table(full_name)
    else:
        print(f"  Creating table '{full_name}'")
        return catalog.create_table(full_name, schema=schema)


def get_file_month(filepath: str) -> str:
    """Extract YYYY-MM from filename like yellow_tripdata_2024-01.parquet."""
    name = Path(filepath).stem
    return name.split("_")[-1]


def _find_canonical_schema(files: list[str]) -> pa.Schema:
    """Find a schema with no null-typed columns (prefer later files)."""
    for f in reversed(files):
        schema = pq.read_schema(f)
        if not any(field.type == pa.null() for field in schema):
            return schema
    # Fallback: use last file's schema but replace null types with double
    schema = pq.read_schema(files[-1])
    fields = []
    for field in schema:
        if field.type == pa.null():
            fields.append(pa.field(field.name, pa.float64(), nullable=True))
        else:
            fields.append(field)
    return pa.schema(fields)


def load_files(files: list[str], table_name: str):
    if not files:
        print(f"No files for {table_name}, skipping")
        return

    files.sort()
    print(f"\nLoading {len(files)} files into {NAMESPACE}.{table_name}")

    canonical_schema = _find_canonical_schema(files)
    iceberg_table = create_or_get_table(table_name, canonical_schema)

    total_rows = 0
    for i, filepath in enumerate(files):
        month = get_file_month(filepath)
        try:
            table = pq.read_table(filepath)

            # Align schema: cast to match the Iceberg table schema if needed
            target_schema = iceberg_table.schema().as_arrow()
            if table.schema != target_schema:
                aligned_columns = []
                for field in target_schema:
                    if field.name in table.column_names:
                        col = table.column(field.name)
                        if col.type == pa.null() or col.type != field.type:
                            # null-typed or mismatched: replace with nulls of target type
                            if col.type == pa.null():
                                col = pa.nulls(len(table), type=field.type)
                            else:
                                col = col.cast(field.type)
                        aligned_columns.append(col)
                    else:
                        aligned_columns.append(
                            pa.nulls(len(table), type=field.type)
                        )
                table = pa.table(aligned_columns, schema=target_schema)

            iceberg_table.append(table)
            total_rows += table.num_rows
            print(f"  [{i+1}/{len(files)}] {month}: {table.num_rows:,} rows")
        except Exception as e:
            print(f"  [{i+1}/{len(files)}] {month}: FAILED - {e}")

    print(f"  Total: {total_rows:,} rows in {NAMESPACE}.{table_name}")


def main():
    ensure_namespace()

    # Gather all valid parquet files
    all_files = sorted(glob.glob(f"{DATA_DIR}/yellow_tripdata_*.parquet"))
    valid_files = []
    for f in all_files:
        try:
            pq.read_schema(f)
            valid_files.append(f)
        except Exception:
            print(f"Skipping incomplete file: {Path(f).name}")

    # Split into legacy (before 2016-07) and modern (2016-07 onwards)
    legacy_files = [f for f in valid_files if get_file_month(f) < CUTOVER]
    modern_files = [f for f in valid_files if get_file_month(f) >= CUTOVER]

    print(f"Found {len(valid_files)} valid files: "
          f"{len(legacy_files)} legacy, {len(modern_files)} modern")

    existing = [t[1] for t in catalog.list_tables(NAMESPACE)]

    # Legacy table: skip if already loaded
    if "yellow_trips_legacy" in existing:
        print(f"\nSkipping nyc.yellow_trips_legacy (already loaded)")
    else:
        load_files(legacy_files, "yellow_trips_legacy")

    # Modern table: drop and reload
    if "yellow_trips" in existing:
        print(f"Dropping existing table nyc.yellow_trips")
        catalog.drop_table(f"{NAMESPACE}.yellow_trips")
    load_files(modern_files, "yellow_trips")

    print("\nDone.")


if __name__ == "__main__":
    main()
