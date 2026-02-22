"""Compact Iceberg retail tables to ~512 MB target file size.

Reads each table, drops it, recreates with write.target-file-size-bytes=512MB,
and writes the data back. Large tables are read/written in batches to control
memory usage.

Usage:
    python scripts/compact_retail_data.py          # from backend/
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pyarrow as pa

from app.warehouse import catalog

TARGET_FILE_SIZE = 512 * 1024 * 1024  # 512 MB

# Tables small enough to read fully into memory (< ~2 GB on disk)
# Everything else gets batched reads.
MEMORY_THRESHOLD_BYTES = 2 * 1024 * 1024 * 1024

RETAIL_NAMESPACES = [
    "retail_catalog__products",
    "retail_catalog__pricing",
    "retail_ops__stores",
    "retail_ops__inventory",
    "retail_sales__customers",
    "retail_sales__transactions",
]

TABLE_PROPERTIES = {
    "write.target-file-size-bytes": str(TARGET_FILE_SIZE),
}


def elapsed(start: float) -> str:
    return f"{time.time() - start:.1f}s"


def get_table_size(table) -> int:
    """Total bytes of all data files."""
    files = table.inspect.data_files().to_pylist()
    return sum(f["file_size_in_bytes"] for f in files)


def get_file_stats(table) -> tuple[int, float]:
    """Return (file_count, avg_size_mb)."""
    files = table.inspect.data_files().to_pylist()
    if not files:
        return 0, 0.0
    sizes = [f["file_size_in_bytes"] for f in files]
    return len(sizes), sum(sizes) / len(sizes) / 1024 / 1024


def compact_table(ns: str, name: str):
    full_id = f"{ns}.{name}"
    tbl = catalog.load_table(full_id)
    total_size = get_table_size(tbl)
    n_files, avg_mb = get_file_stats(tbl)

    # Skip if already close to target (within 20%)
    if avg_mb >= TARGET_FILE_SIZE / 1024 / 1024 * 0.8:
        print(f"  {name}: {n_files} files, avg {avg_mb:.1f} MB — already optimal, skipping")
        return

    print(f"  {name}: {n_files} files, avg {avg_mb:.1f} MB, total {total_size / 1024**3:.1f} GB")
    schema = tbl.schema().as_arrow()
    t0 = time.time()

    if total_size <= MEMORY_THRESHOLD_BYTES:
        # Small table: read all at once
        print(f"    Reading all data...")
        data = tbl.scan().to_arrow()
        print(f"    Read {data.num_rows:,} rows ({elapsed(t0)})")

        catalog.drop_table(full_id)
        new_tbl = catalog.create_table(full_id, schema=schema, properties=TABLE_PROPERTIES)
        new_tbl.append(data)
    else:
        # Large table: stream from old table into a temp table, then swap.
        # This avoids loading everything into memory at once.
        tmp_name = f"{name}__compacting"
        tmp_id = f"{ns}.{tmp_name}"

        # Clean up any leftover temp table
        try:
            catalog.drop_table(tmp_id)
        except Exception:
            pass

        new_tbl = catalog.create_table(tmp_id, schema=schema, properties=TABLE_PROPERTIES)
        print(f"    Streaming into temp table...")

        # Estimate total rows from file-level stats
        files = tbl.inspect.data_files().to_pylist()
        est_total = sum(f.get("record_count", 0) for f in files)

        written = 0
        chunk_batches = []
        chunk_rows = 0
        for batch in tbl.scan().to_arrow_batch_reader():
            chunk_batches.append(batch)
            chunk_rows += batch.num_rows
            if chunk_rows >= 20_000_000:
                arrow_table = pa.Table.from_batches(chunk_batches, schema=schema)
                new_tbl.append(arrow_table)
                written += chunk_rows
                pct = written * 100 // est_total if est_total else 0
                print(f"    Written {written:>14,}/{est_total:,} rows ({pct}%) ({elapsed(t0)})")
                chunk_batches = []
                chunk_rows = 0

        if chunk_batches:
            arrow_table = pa.Table.from_batches(chunk_batches, schema=schema)
            new_tbl.append(arrow_table)
            written += chunk_rows
            print(f"    Written {written:>14,}/{est_total:,} rows (100%) ({elapsed(t0)})")

        # Swap: drop original, rename temp → original
        catalog.drop_table(full_id)
        catalog.rename_table(tmp_id, full_id)

    n_files_after, avg_mb_after = get_file_stats(catalog.load_table(full_id))
    print(f"    Compacted: {n_files} files → {n_files_after} files, avg {avg_mb_after:.1f} MB ({elapsed(t0)})")


def main():
    t_start = time.time()
    print("=" * 60)
    print(f"Compacting retail tables (target: {TARGET_FILE_SIZE // 1024 // 1024} MB)")
    print("=" * 60)

    for ns in RETAIL_NAMESPACES:
        tables = catalog.list_tables(ns)
        if not tables:
            continue
        print(f"\n--- {ns} ---")
        for t in tables:
            # Skip temp tables from interrupted compactions
            if t[1].endswith("__compacting"):
                continue
            compact_table(ns, t[1])

    print(f"\n{'=' * 60}")
    print(f"All done in {elapsed(t_start)}")
    print(f"{'=' * 60}")
    print("\nRestart the backend to re-register tables in the catalog.")


if __name__ == "__main__":
    main()
