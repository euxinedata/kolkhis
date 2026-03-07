"""Generate small sample retail data for quick testing.

Creates the same 3-database structure as generate_retail_data.py but with
much smaller row counts (~1K-50K rows per table, no chunked writes).

Usage:
    cd backend/
    python scripts/generate_org_data.py <org_uuid>
"""

import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pyarrow as pa
from faker import Faker
from sqlalchemy import create_engine, text

from app.config import DATABASE_URL_SYNC
from scripts.generate_retail_data import (
    COUNTRIES,
    DATABASES,
    DEPT_TREE,
    create_table_from_arrow,
    elapsed,
    get_ducklake_conn,
    insert_org_database,
    random_dates,
    random_timestamps,
)

SEED = 42
fake = Faker()
Faker.seed(SEED)
rng = np.random.default_rng(SEED)

# Small dimension sizes for testing
N_CATEGORIES = 50
N_BRANDS = 100
N_SUPPLIERS = 200
N_PRODUCTS = 1_000
N_REGIONS = 20
N_STORES = 50


def gen_categories() -> pa.Table:
    ids, names, parents = [], [], []
    cat_id = 0
    for dept, categories in DEPT_TREE.items():
        cat_id += 1
        dept_id = cat_id
        ids.append(dept_id)
        names.append(dept)
        parents.append(None)
        for cat, subcats in categories.items():
            cat_id += 1
            mid_id = cat_id
            ids.append(mid_id)
            names.append(cat)
            parents.append(dept_id)
            for sub in subcats:
                cat_id += 1
                ids.append(cat_id)
                names.append(sub)
                parents.append(mid_id)
    while len(ids) < N_CATEGORIES:
        cat_id += 1
        ids.append(cat_id)
        names.append(f"Category_{cat_id}")
        parents.append(rng.integers(1, len(ids)))
    return pa.table({
        "category_id": pa.array(ids[:N_CATEGORIES], type=pa.int32()),
        "name": pa.array(names[:N_CATEGORIES], type=pa.string()),
        "parent_category_id": pa.array(parents[:N_CATEGORIES], type=pa.int32()),
    })


def gen_brands() -> pa.Table:
    names = [fake.company() for _ in range(N_BRANDS)]
    countries = rng.choice(COUNTRIES, N_BRANDS).tolist()
    return pa.table({
        "brand_id": pa.array(np.arange(1, N_BRANDS + 1, dtype=np.int32)),
        "name": pa.array(names, type=pa.string()),
        "country_of_origin": pa.array(countries, type=pa.string()),
    })


def gen_suppliers() -> pa.Table:
    names = [fake.company() for _ in range(N_SUPPLIERS)]
    countries = rng.choice(COUNTRIES, N_SUPPLIERS).tolist()
    emails = [fake.company_email() for _ in range(N_SUPPLIERS)]
    phones = [fake.phone_number() for _ in range(N_SUPPLIERS)]
    return pa.table({
        "supplier_id": pa.array(np.arange(1, N_SUPPLIERS + 1, dtype=np.int32)),
        "name": pa.array(names, type=pa.string()),
        "country": pa.array(countries, type=pa.string()),
        "contact_email": pa.array(emails, type=pa.string()),
        "phone": pa.array(phones, type=pa.string()),
    })


def gen_products() -> pa.Table:
    skus = [f"SKU-{i:05d}" for i in range(1, N_PRODUCTS + 1)]
    names = [fake.catch_phrase() for _ in range(N_PRODUCTS)]
    brand_ids = rng.integers(1, N_BRANDS + 1, size=N_PRODUCTS, dtype=np.int32)
    cat_ids = rng.integers(1, N_CATEGORIES + 1, size=N_PRODUCTS, dtype=np.int32)
    sup_ids = rng.integers(1, N_SUPPLIERS + 1, size=N_PRODUCTS, dtype=np.int32)
    weights = np.round(rng.uniform(0.01, 50.0, size=N_PRODUCTS), 2).astype(np.float32)
    units = rng.choice(["each", "kg", "liter", "pack"], N_PRODUCTS).tolist()
    created = random_timestamps(date(2020, 1, 1), date(2024, 12, 31), N_PRODUCTS)
    return pa.table({
        "product_id": pa.array(np.arange(1, N_PRODUCTS + 1, dtype=np.int32)),
        "sku": pa.array(skus, type=pa.string()),
        "name": pa.array(names, type=pa.string()),
        "brand_id": pa.array(brand_ids),
        "category_id": pa.array(cat_ids),
        "supplier_id": pa.array(sup_ids),
        "weight_kg": pa.array(weights),
        "unit_of_measure": pa.array(units, type=pa.string()),
        "created_at": created,
    })


def gen_regions() -> pa.Table:
    region_names = []
    region_countries = []
    for country in COUNTRIES[:4]:
        for i in range(5):
            region_names.append(f"{country}-Region-{i+1}")
            region_countries.append(country)
    return pa.table({
        "region_id": pa.array(np.arange(1, N_REGIONS + 1, dtype=np.int32)),
        "name": pa.array(region_names[:N_REGIONS], type=pa.string()),
        "country": pa.array(region_countries[:N_REGIONS], type=pa.string()),
    })


def gen_stores() -> pa.Table:
    names = [f"Store {fake.city()} #{i}" for i in range(1, N_STORES + 1)]
    region_ids = rng.integers(1, N_REGIONS + 1, size=N_STORES, dtype=np.int32)
    types = rng.choice(["offline", "online"], N_STORES, p=[0.8, 0.2]).tolist()
    addresses = [fake.street_address() for _ in range(N_STORES)]
    cities = [fake.city() for _ in range(N_STORES)]
    countries = rng.choice(COUNTRIES, N_STORES).tolist()
    lats = np.round(rng.uniform(-60, 70, size=N_STORES), 6).astype(np.float64)
    lons = np.round(rng.uniform(-180, 180, size=N_STORES), 6).astype(np.float64)
    opened = random_dates(date(2010, 1, 1), date(2024, 12, 31), N_STORES)
    return pa.table({
        "store_id": pa.array(np.arange(1, N_STORES + 1, dtype=np.int32)),
        "name": pa.array(names, type=pa.string()),
        "region_id": pa.array(region_ids),
        "store_type": pa.array(types, type=pa.string()),
        "address": pa.array(addresses, type=pa.string()),
        "city": pa.array(cities, type=pa.string()),
        "country": pa.array(countries, type=pa.string()),
        "lat": pa.array(lats),
        "lon": pa.array(lons),
        "opened_at": opened,
    })


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/generate_org_data.py <org_uuid>")
        sys.exit(1)

    org_id = sys.argv[1]
    t_start = time.time()

    print(f"Generating sample data for org {org_id}")

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

    # Provision databases and create DuckLake connections
    connections = {}
    for db_name, schemas in DATABASES.items():
        print(f"\nProvisioning database: {db_name}")
        insert_org_database(engine, org_id, db_name)
        duck = get_ducklake_conn(org_id, db_name)
        connections[db_name] = duck
        for schema in schemas:
            duck.execute(f'CREATE SCHEMA IF NOT EXISTS "{db_name}"."{schema}"')
            print(f"  Ensured schema {schema}")

    # retail_catalog.products
    duck = connections["retail_catalog"]
    for name, tbl in [("categories", gen_categories()), ("brands", gen_brands()),
                      ("suppliers", gen_suppliers()), ("products", gen_products())]:
        create_table_from_arrow(duck, "retail_catalog", "products", name, tbl)
        print(f"  products.{name}: {tbl.num_rows:,} rows")

    # retail_ops.stores
    duck = connections["retail_ops"]
    for name, tbl in [("regions", gen_regions()), ("stores", gen_stores())]:
        create_table_from_arrow(duck, "retail_ops", "stores", name, tbl)
        print(f"  stores.{name}: {tbl.num_rows:,} rows")

    # Clean up connections
    for duck in connections.values():
        duck.close()

    print(f"\nDone in {elapsed(t_start)}")
    print("\nRestart the backend to pick up the new databases.")


if __name__ == "__main__":
    main()
