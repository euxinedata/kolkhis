---
name: data
description: Data loading conventions for Kolkhis Iceberg warehouse
user-invocable: false
---

# Data Loading Conventions

## Script Location

Data loading scripts live in `backend/scripts/`. Run from `backend/` directory with the app modules on the path.

## Loading Pattern

Reference implementation: `backend/scripts/load_taxi_data.py`

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pyarrow.parquet as pq
from app.warehouse import catalog

PARQUET_FILE = "/tmp/kolkhis-data/my_data.parquet"
NAMESPACE = "my_namespace"
TABLE_NAME = "my_table"

def main():
    table = pq.read_table(PARQUET_FILE)

    # Create namespace if needed
    existing_ns = [ns[0] for ns in catalog.list_namespaces()]
    if NAMESPACE not in existing_ns:
        catalog.create_namespace(NAMESPACE)

    # Create or replace table
    full_name = f"{NAMESPACE}.{TABLE_NAME}"
    existing_tables = [t[1] for t in catalog.list_tables(NAMESPACE)]
    if TABLE_NAME in existing_tables:
        catalog.drop_table(full_name)

    iceberg_table = catalog.create_table(full_name, schema=table.schema)
    iceberg_table.append(table)

    # Verify
    loaded = catalog.load_table(full_name)
    result = loaded.scan().to_arrow()
    print(f"{result.num_rows} rows loaded into {full_name}.")

if __name__ == "__main__":
    main()
```

## Directory Conventions

- Raw data files: `/tmp/kolkhis-data/`
- Iceberg warehouse: configured via `WAREHOUSE_PATH` (default `/mnt/warehouse`)

## Schema Handling

When loading Parquet files with schema issues:
- Prefer files with concrete types as the canonical schema (avoid null-typed columns)
- Use `pa.schema()` with explicit `pa.field()` definitions when creating tables via API
- Supported type mappings (for API table creation): `string`, `int`/`integer`/`int32`, `int64`/`long`, `float`/`float32`, `float64`/`double`, `boolean`/`bool`, `date`, `timestamp`

## Key Files

- `backend/app/warehouse.py` — PyIceberg `SqlCatalog` singleton
- `backend/scripts/load_taxi_data.py` — Reference loading script
- `backend/app/routers/catalog.py` — REST API for namespace/table management
