"""Compact DuckLake retail tables by rewriting them.

Reads each table, drops it, recreates it via CTAS. This consolidates
fragmented parquet files created by chunked inserts.

Usage:
    cd backend/
    python scripts/compact_retail_data.py <org_uuid>
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text

from app.config import DATABASE_URL_SYNC
from scripts.generate_retail_data import DATABASES, get_ducklake_conn


def elapsed(start: float) -> str:
    return f"{time.time() - start:.1f}s"


def compact_table(conn, db_name: str, schema: str, table_name: str):
    """Compact a table by recreating it via CTAS."""
    full = f'"{db_name}"."{schema}"."{table_name}"'
    tmp_name = f"{table_name}__compacting"
    tmp_full = f'"{db_name}"."{schema}"."{tmp_name}"'

    try:
        row_count = conn.execute(f"SELECT count(*) FROM {full}").fetchone()[0]
    except Exception as e:
        print(f"  {table_name}: FAILED to read - {e}")
        return

    print(f"  {table_name}: {row_count:,} rows")
    t0 = time.time()

    try:
        conn.execute(f"DROP TABLE IF EXISTS {tmp_full}")
    except Exception:
        pass

    conn.execute(f"CREATE TABLE {tmp_full} AS SELECT * FROM {full}")
    conn.execute(f"DROP TABLE {full}")
    conn.execute(f"ALTER TABLE {tmp_full} RENAME TO {table_name}")

    print(f"    Compacted in {elapsed(t0)}")


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
            text("SELECT name FROM org_databases WHERE org_id = :org_id"),
            {"org_id": org_id},
        ).fetchall()

    if not rows:
        print(f"No databases found for org {org_id}")
        sys.exit(1)

    db_names = {r[0] for r in rows}

    print("=" * 60)
    print("Compacting retail tables")
    print("=" * 60)

    for db_name, schemas in DATABASES.items():
        if db_name not in db_names:
            print(f"\nSkipping {db_name} — not found in org_databases")
            continue

        duck = get_ducklake_conn(org_id, db_name)
        for ns in schemas:
            # List tables in schema
            try:
                result = duck.execute(
                    f"SELECT table_name FROM information_schema.tables "
                    f"WHERE table_catalog = '{db_name}' AND table_schema = '{ns}'"
                ).fetchall()
            except Exception:
                continue
            tables = [r[0] for r in result]
            if not tables:
                continue
            print(f"\n--- {db_name}.{ns} ---")
            for tbl_name in tables:
                if tbl_name.endswith("__compacting"):
                    continue
                compact_table(duck, db_name, ns, tbl_name)
        duck.close()

    print(f"\n{'=' * 60}")
    print(f"All done in {elapsed(t_start)}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
