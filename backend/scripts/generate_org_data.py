"""Generate sample retail data into an org's S3 bucket.

Usage:
    python scripts/generate_org_data.py <org_uuid>
"""

import sys
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pyarrow as pa
from faker import Faker
from pyiceberg.catalog.sql import SqlCatalog

from app.config import (
    DATABASE_URL_PLAIN, S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY, S3_REGION,
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

COUNTRIES = ["US", "GB", "DE", "FR", "JP", "CA", "AU", "BR", "IN", "CN"]

DEPT_TREE = {
    "Electronics": {
        "Computers": ["Laptops", "Desktops", "Tablets"],
        "Phones": ["Smartphones", "Accessories"],
        "Audio": ["Headphones", "Speakers"],
    },
    "Clothing": {
        "Men": ["Shirts", "Pants", "Shoes"],
        "Women": ["Dresses", "Tops", "Shoes"],
    },
    "Home & Garden": {
        "Furniture": ["Living Room", "Bedroom", "Office"],
        "Kitchen": ["Cookware", "Appliances"],
    },
}


def elapsed(start: float) -> str:
    return f"{time.time() - start:.1f}s"


def random_dates(start: date, end: date, n: int) -> pa.Array:
    days = (end - start).days
    offsets = rng.integers(0, days + 1, size=n)
    epoch = date(1970, 1, 1)
    base = (start - epoch).days
    return pa.array((base + offsets).astype(np.int32), type=pa.date32())


def random_timestamps(start: date, end: date, n: int) -> pa.Array:
    start_ts = int(datetime(start.year, start.month, start.day).timestamp())
    end_ts = int(datetime(end.year, end.month, end.day, 23, 59, 59).timestamp())
    ts = rng.integers(start_ts, end_ts + 1, size=n)
    return pa.array(ts * 1_000_000, type=pa.timestamp("us"))


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
    warehouse_path = f"s3://{org_id}/warehouse"

    print(f"Generating data for org {org_id}")
    print(f"Warehouse: {warehouse_path}")

    catalog = SqlCatalog(
        f"org-{org_id}",
        uri=DATABASE_URL_PLAIN,
        warehouse=warehouse_path,
        **{
            "s3.endpoint": S3_ENDPOINT,
            "s3.access-key-id": S3_ACCESS_KEY,
            "s3.secret-access-key": S3_SECRET_KEY,
            "s3.region": S3_REGION,
        },
    )

    # Create namespaces
    ns = "retail.products"
    for n in ["retail", "retail.products", "retail.stores"]:
        existing = {".".join(t) for t in catalog.list_namespaces()}
        if n not in existing:
            catalog.create_namespace(n)
            print(f"  Created namespace {n}")

    # retail.products
    t0 = time.time()
    tables = {
        "categories": gen_categories(),
        "brands": gen_brands(),
        "suppliers": gen_suppliers(),
        "products": gen_products(),
    }
    for name, tbl in tables.items():
        full = f"retail.products.{name}"
        existing = {t[-1] for t in catalog.list_tables("retail.products")}
        if name in existing:
            catalog.drop_table(full)
        it = catalog.create_table(full, schema=tbl.schema)
        it.append(tbl)
        print(f"  {full}: {tbl.num_rows:,} rows")

    # retail.stores
    tables = {
        "regions": gen_regions(),
        "stores": gen_stores(),
    }
    for name, tbl in tables.items():
        full = f"retail.stores.{name}"
        existing = {t[-1] for t in catalog.list_tables("retail.stores")}
        if name in existing:
            catalog.drop_table(full)
        it = catalog.create_table(full, schema=tbl.schema)
        it.append(tbl)
        print(f"  {full}: {tbl.num_rows:,} rows")

    print(f"\nDone in {elapsed(t0)}")


if __name__ == "__main__":
    main()
