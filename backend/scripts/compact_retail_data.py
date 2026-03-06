"""Compact Iceberg retail tables to ~512 MB target file size.

Reads each table, drops it, recreates with write.target-file-size-bytes=512MB,
and writes the data back. Large tables are read/written in batches to control
memory usage.

Usage:
    cd backend/
    python scripts/compact_retail_data.py <org_uuid>
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pyarrow as pa
from sqlalchemy import create_engine, text

from app.config import DATABASE_URL_SYNC
from scripts.generate_retail_data import DATABASES, get_catalog

TARGET_FILE_SIZE = 512 * 1024 * 1024  # 512 MB

# Tables small enough to read fully into memory (< ~2 GB on disk)
MEMORY_THRESHOLD_BYTES = 2 * 1024 * 1024 * 1024


def elapsed(start: float) -> str:
    return f"{time.time() - start:.1f}s"


def get_table_size(table) -> int:
    files = table.inspect.data_files().to_pylist()
    return sum(f["file_size_in_bytes"] for f in files)


def get_file_stats(table) -> tuple[int, float]:
    files = table.inspect.data_files().to_pylist()
    if not files:
        return 0, 0.0
    sizes = [f["file_size_in_bytes"] for f in files]
    return len(sizes), sum(sizes) / len(sizes) / 1024 / 1024


def estimate_compression_ratio(tbl) -> float:
    batch = next(tbl.scan().to_arrow_batch_reader())
    mem_per_row = batch.nbytes / batch.num_rows
    files = tbl.inspect.data_files().to_pylist()
    disk_bytes = sum(f["file_size_in_bytes"] for f in files)
    disk_rows = sum(f["record_count"] for f in files)
    disk_per_row = disk_bytes / disk_rows if disk_rows else mem_per_row
    return mem_per_row / disk_per_row if disk_per_row else 1.0


def compact_table(catalog, ns: str, name: str):
    full_id = f"{ns}.{name}"
    tbl = catalog.load_table(full_id)
    total_size = get_table_size(tbl)
    n_files, avg_mb = get_file_stats(tbl)

    if avg_mb >= TARGET_FILE_SIZE / 1024 / 1024 * 0.8:
        print(f"  {name}: {n_files} files, avg {avg_mb:.1f} MB — already optimal, skipping")
        return

    print(f"  {name}: {n_files} files, avg {avg_mb:.1f} MB, total {total_size / 1024**3:.1f} GB")
    schema = tbl.schema().as_arrow()
    t0 = time.time()

    ratio = estimate_compression_ratio(tbl)
    effective_target = int(TARGET_FILE_SIZE * ratio * 1.5)
    props = {"write.target-file-size-bytes": str(effective_target)}

    if total_size <= MEMORY_THRESHOLD_BYTES:
        print(f"    Reading all data...")
        data = tbl.scan().to_arrow()
        print(f"    Read {data.num_rows:,} rows ({elapsed(t0)})")

        catalog.drop_table(full_id)
        new_tbl = catalog.create_table(full_id, schema=schema, properties=props)
        new_tbl.append(data)
    else:
        tmp_name = f"{name}__compacting"
        tmp_id = f"{ns}.{tmp_name}"

        try:
            catalog.drop_table(tmp_id)
        except Exception:
            pass

        new_tbl = catalog.create_table(tmp_id, schema=schema, properties=props)
        print(f"    Streaming into temp table...")

        files = tbl.inspect.data_files().to_pylist()
        est_total = sum(f.get("record_count", 0) for f in files)

        bytes_per_row = total_size / est_total if est_total else 20
        flush_rows = int(TARGET_FILE_SIZE * 2 / bytes_per_row)
        print(f"    compression ratio {ratio:.1f}x, effective target {effective_target // 1024 // 1024} MB in-memory")
        print(f"    ~{bytes_per_row:.0f} bytes/row on disk, flushing every {flush_rows:,} rows")

        written = 0
        chunk_batches = []
        chunk_rows = 0
        for batch in tbl.scan().to_arrow_batch_reader():
            chunk_batches.append(batch)
            chunk_rows += batch.num_rows
            if chunk_rows >= flush_rows:
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

        catalog.drop_table(full_id)
        catalog.rename_table(tmp_id, full_id)

    n_files_after, avg_mb_after = get_file_stats(catalog.load_table(full_id))
    print(f"    Compacted: {n_files} files -> {n_files_after} files, avg {avg_mb_after:.1f} MB ({elapsed(t0)})")


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/compact_retail_data.py <org_uuid>")
        sys.exit(1)

    org_id = sys.argv[1]
    t_start = time.time()

    # Load org databases from DB
    engine = create_engine(DATABASE_URL_SYNC)
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name, lakekeeper_warehouse FROM org_databases WHERE org_id = :org_id"),
            {"org_id": org_id},
        ).fetchall()

    if not rows:
        print(f"No databases found for org {org_id}")
        sys.exit(1)

    db_map = {r[0]: r[1] for r in rows}

    print("=" * 60)
    print(f"Compacting retail tables (target: {TARGET_FILE_SIZE // 1024 // 1024} MB)")
    print("=" * 60)

    for db_name, schemas in DATABASES.items():
        warehouse_name = db_map.get(db_name)
        if not warehouse_name:
            print(f"\nSkipping {db_name} — not found in org_databases")
            continue

        cat = get_catalog(warehouse_name)
        for ns in schemas:
            try:
                tables = cat.list_tables(ns)
            except Exception:
                continue
            if not tables:
                continue
            print(f"\n--- {db_name}.{ns} ---")
            for t in tables:
                if t[-1].endswith("__compacting"):
                    continue
                compact_table(cat, ns, t[-1])

    print(f"\n{'=' * 60}")
    print(f"All done in {elapsed(t_start)}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
