"""Generate synthetic retail data for stress-testing the Kolkhis SQL editor.

Creates ~5.3B rows across 3 databases, 6 schemas, and 17 tables modelling
a multi-national retail chain with offline and online stores.

Usage:
    python -m scripts.generate_retail_data          # from backend/
    python scripts/generate_retail_data.py          # also works
"""

import io
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from faker import Faker

from app.warehouse import catalog

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SEED = 42
CHUNK_SIZE = 2_000_000                       # rows per generation chunk
TARGET_DISK_SIZE = 512 * 1024 * 1024         # 512 MB Parquet files on disk
BIN_PACKER_BYPASS = 10 * 1024 * 1024 * 1024  # 10 GB — prevents bin-packer from splitting

# Namespace mapping: database__schema
NAMESPACES = {
    "retail_catalog__products": "retail_catalog__products",
    "retail_catalog__pricing": "retail_catalog__pricing",
    "retail_ops__stores": "retail_ops__stores",
    "retail_ops__inventory": "retail_ops__inventory",
    "retail_sales__transactions": "retail_sales__transactions",
    "retail_sales__customers": "retail_sales__customers",
}

# Row counts
N_CATEGORIES = 200
N_BRANDS = 500
N_SUPPLIERS = 1_000
N_PRODUCTS = 50_000
N_PRICE_LISTS = 100_000
N_PROMOTIONS = 5_000
N_PROMO_PRODUCTS = 50_000
N_REGIONS = 50
N_STORES = 500
N_EMPLOYEES = 10_000
N_STOCK_LEVELS = 5_000_000
N_REPLENISHMENT = 20_000_000
N_ORDERS = 1_000_000_000
N_ORDER_LINES = 3_000_000_000
N_PAYMENTS = 1_000_000_000
N_CUSTOMERS = 20_000_000
N_LOYALTY = 10_000_000

# Date range for orders
ORDER_START = date(2022, 1, 1)
ORDER_END = date(2025, 12, 31)
ORDER_DAYS = (ORDER_END - ORDER_START).days + 1

# Countries and currencies
COUNTRY_CURRENCIES = {
    "US": "USD", "GB": "GBP", "DE": "EUR", "FR": "EUR", "JP": "JPY",
    "CA": "CAD", "AU": "AUD", "BR": "BRL", "IN": "INR", "CN": "CNY",
    "MX": "MXN", "IT": "EUR", "ES": "EUR", "NL": "EUR", "SE": "SEK",
    "NO": "NOK", "DK": "DKK", "PL": "PLN", "KR": "KRW", "SG": "SGD",
}
COUNTRIES = list(COUNTRY_CURRENCIES.keys())
CURRENCIES = list(set(COUNTRY_CURRENCIES.values()))

# Product departments → categories → subcategories (3-level hierarchy)
DEPT_TREE = {
    "Electronics": {
        "Computers": ["Laptops", "Desktops", "Tablets", "Monitors"],
        "Phones": ["Smartphones", "Feature Phones", "Accessories"],
        "Audio": ["Headphones", "Speakers", "Soundbars"],
        "Gaming": ["Consoles", "Controllers", "Games"],
    },
    "Clothing": {
        "Men": ["Shirts", "Pants", "Jackets", "Shoes"],
        "Women": ["Dresses", "Tops", "Skirts", "Shoes"],
        "Kids": ["Boys", "Girls", "Infants"],
        "Sportswear": ["Running", "Gym", "Outdoor"],
    },
    "Home & Garden": {
        "Furniture": ["Living Room", "Bedroom", "Office", "Outdoor"],
        "Kitchen": ["Cookware", "Appliances", "Utensils"],
        "Garden": ["Tools", "Plants", "Decor"],
        "Lighting": ["Indoor", "Outdoor", "Smart"],
    },
    "Food & Beverage": {
        "Fresh": ["Produce", "Dairy", "Meat", "Seafood"],
        "Packaged": ["Snacks", "Canned", "Frozen", "Dry Goods"],
        "Beverages": ["Soft Drinks", "Coffee & Tea", "Juice", "Water"],
    },
    "Health & Beauty": {
        "Personal Care": ["Skincare", "Haircare", "Oral Care"],
        "Cosmetics": ["Makeup", "Fragrance", "Nails"],
        "Wellness": ["Vitamins", "Supplements", "First Aid"],
    },
    "Sports & Outdoors": {
        "Fitness": ["Weights", "Cardio", "Yoga", "Accessories"],
        "Outdoor": ["Camping", "Hiking", "Fishing"],
        "Team Sports": ["Soccer", "Basketball", "Baseball"],
    },
    "Books & Media": {
        "Books": ["Fiction", "Non-Fiction", "Children", "Academic"],
        "Music": ["CDs", "Vinyl", "Digital"],
        "Movies": ["DVD", "Blu-ray", "Streaming"],
    },
    "Toys & Games": {
        "Action Figures": ["Superheroes", "Vehicles", "Animals"],
        "Board Games": ["Strategy", "Family", "Party"],
        "Educational": ["STEM", "Arts", "Puzzles"],
    },
}

EMPLOYEE_ROLES = ["cashier", "manager", "stock_clerk", "sales_associate"]
CHANNELS = ["pos", "web", "app"]
PAYMENT_METHODS = ["cash", "card", "mobile"]
LOYALTY_TIERS = ["bronze", "silver", "gold", "platinum"]

fake = Faker()
Faker.seed(SEED)
rng = np.random.default_rng(SEED)

# Pre-build name/city/domain pools for fast vectorized sampling (avoids faker per-row)
_FIRST_NAMES = [fake.first_name() for _ in range(5_000)]
_LAST_NAMES = [fake.last_name() for _ in range(5_000)]
_CITIES = [fake.city() for _ in range(2_000)]
_DOMAINS = ["gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
            "mail.com", "proton.me", "aol.com", "zoho.com", "yandex.com"]
_FIRST_NAMES_ARR = np.array(_FIRST_NAMES)
_LAST_NAMES_ARR = np.array(_LAST_NAMES)
_CITIES_ARR = np.array(_CITIES)
_DOMAINS_ARR = np.array(_DOMAINS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def elapsed(start: float) -> str:
    return f"{time.time() - start:.1f}s"


def calibrate_parquet_bytes_per_row(sample: pa.Table) -> float:
    """Write a sample to an in-memory buffer to measure actual Parquet bytes/row."""
    buf = io.BytesIO()
    pq.write_table(sample, buf, compression='zstd')
    return len(buf.getvalue()) / sample.num_rows


def ensure_namespace(ns: str):
    existing = {t[0] for t in catalog.list_namespaces()}
    if ns not in existing:
        catalog.create_namespace(ns)
        print(f"  Created namespace {ns}")


def create_table(ns: str, name: str, schema: pa.Schema):
    full = f"{ns}.{name}"
    existing = {t[1] for t in catalog.list_tables(ns)}
    if name in existing:
        print(f"  Table {full} already exists, dropping")
        catalog.drop_table(full)
    print(f"  Creating {full}")
    props = {"write.target-file-size-bytes": str(BIN_PACKER_BYPASS)}
    return catalog.create_table(full, schema=schema, properties=props)


def random_dates(start: date, end: date, n: int) -> pa.Array:
    """Generate n random dates between start and end as pa.date32()."""
    days = (end - start).days
    offsets = rng.integers(0, days + 1, size=n)
    epoch = date(1970, 1, 1)
    base = (start - epoch).days
    return pa.array((base + offsets).astype(np.int32), type=pa.date32())


def random_timestamps(start: date, end: date, n: int) -> pa.Array:
    """Generate n random timestamps between start and end."""
    start_ts = int(datetime(start.year, start.month, start.day).timestamp())
    end_ts = int(datetime(end.year, end.month, end.day, 23, 59, 59).timestamp())
    ts = rng.integers(start_ts, end_ts + 1, size=n)
    return pa.array(ts * 1_000_000, type=pa.timestamp("us"))


# ---------------------------------------------------------------------------
# Table generators
# ---------------------------------------------------------------------------


def gen_categories() -> pa.Table:
    """Generate 3-level category hierarchy."""
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

    # Pad to N_CATEGORIES if needed
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


def gen_price_lists() -> pa.Table:
    product_ids = rng.integers(1, N_PRODUCTS + 1, size=N_PRICE_LISTS, dtype=np.int32)
    currencies = rng.choice(CURRENCIES, N_PRICE_LISTS).tolist()
    prices = np.round(rng.uniform(0.99, 999.99, size=N_PRICE_LISTS), 2).astype(np.float64)
    valid_from = random_dates(date(2022, 1, 1), date(2025, 6, 30), N_PRICE_LISTS)
    # valid_to is 90-365 days after valid_from
    from_days = valid_from.to_pylist()
    offsets = rng.integers(90, 366, size=N_PRICE_LISTS)
    valid_to_list = [(d + timedelta(days=int(o)) if d else None)
                     for d, o in zip(from_days, offsets)]
    return pa.table({
        "price_list_id": pa.array(np.arange(1, N_PRICE_LISTS + 1, dtype=np.int32)),
        "product_id": pa.array(product_ids),
        "currency": pa.array(currencies, type=pa.string()),
        "unit_price": pa.array(prices, type=pa.float64()),
        "valid_from": valid_from,
        "valid_to": pa.array(valid_to_list, type=pa.date32()),
    })


def gen_promotions() -> pa.Table:
    names = [f"Promo {fake.word().title()} {i}" for i in range(1, N_PROMOTIONS + 1)]
    discounts = np.round(rng.uniform(5.0, 50.0, size=N_PROMOTIONS), 1).astype(np.float32)
    start_dates = random_dates(date(2022, 1, 1), date(2025, 10, 31), N_PROMOTIONS)
    start_list = start_dates.to_pylist()
    end_list = [(d + timedelta(days=int(rng.integers(7, 90))) if d else None)
                for d in start_list]
    min_qty = rng.integers(1, 10, size=N_PROMOTIONS, dtype=np.int32)
    return pa.table({
        "promotion_id": pa.array(np.arange(1, N_PROMOTIONS + 1, dtype=np.int32)),
        "name": pa.array(names, type=pa.string()),
        "discount_pct": pa.array(discounts),
        "start_date": start_dates,
        "end_date": pa.array(end_list, type=pa.date32()),
        "min_qty": pa.array(min_qty),
    })


def gen_promotion_products() -> pa.Table:
    promo_ids = rng.integers(1, N_PROMOTIONS + 1, size=N_PROMO_PRODUCTS, dtype=np.int32)
    product_ids = rng.integers(1, N_PRODUCTS + 1, size=N_PROMO_PRODUCTS, dtype=np.int32)
    return pa.table({
        "promotion_id": pa.array(promo_ids),
        "product_id": pa.array(product_ids),
    })


def gen_regions() -> pa.Table:
    region_names = []
    region_countries = []
    for country in COUNTRIES[:10]:
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
    # 400 offline, 100 online
    types = (["offline"] * 400 + ["online"] * 100)[:N_STORES]
    rng.shuffle(types)
    addresses = [fake.street_address() for _ in range(N_STORES)]
    cities = [fake.city() for _ in range(N_STORES)]
    countries = rng.choice(COUNTRIES, N_STORES).tolist()
    lats = np.round(rng.uniform(-60, 70, size=N_STORES), 6).astype(np.float64)
    lons = np.round(rng.uniform(-180, 180, size=N_STORES), 6).astype(np.float64)
    opened = random_dates(date(2010, 1, 1), date(2024, 12, 31), N_STORES)
    # ~5% closed
    closed_list = []
    opened_list = opened.to_pylist()
    for i in range(N_STORES):
        if rng.random() < 0.05 and opened_list[i]:
            closed_list.append(
                opened_list[i] + timedelta(days=int(rng.integers(365, 3650)))
            )
        else:
            closed_list.append(None)

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
        "closed_at": pa.array(closed_list, type=pa.date32()),
    })


def gen_employees() -> pa.Table:
    first_names = [fake.first_name() for _ in range(N_EMPLOYEES)]
    last_names = [fake.last_name() for _ in range(N_EMPLOYEES)]
    store_ids = rng.integers(1, N_STORES + 1, size=N_EMPLOYEES, dtype=np.int32)
    roles = rng.choice(EMPLOYEE_ROLES, N_EMPLOYEES).tolist()
    hire_dates = random_dates(date(2015, 1, 1), date(2025, 6, 30), N_EMPLOYEES)
    rates = np.round(rng.uniform(12.0, 55.0, size=N_EMPLOYEES), 2).astype(np.float32)
    return pa.table({
        "employee_id": pa.array(np.arange(1, N_EMPLOYEES + 1, dtype=np.int32)),
        "store_id": pa.array(store_ids),
        "first_name": pa.array(first_names, type=pa.string()),
        "last_name": pa.array(last_names, type=pa.string()),
        "role": pa.array(roles, type=pa.string()),
        "hire_date": hire_dates,
        "hourly_rate": pa.array(rates),
    })


def gen_stock_levels_chunk(chunk_start: int, chunk_size: int) -> pa.Table:
    n = min(chunk_size, N_STOCK_LEVELS - chunk_start)
    store_ids = rng.integers(1, N_STORES + 1, size=n, dtype=np.int32)
    product_ids = rng.integers(1, N_PRODUCTS + 1, size=n, dtype=np.int32)
    on_hand = rng.integers(0, 500, size=n, dtype=np.int32)
    reserved = np.minimum(rng.integers(0, 50, size=n, dtype=np.int32), on_hand)
    recount = random_timestamps(date(2024, 1, 1), date(2025, 12, 31), n)
    return pa.table({
        "store_id": pa.array(store_ids),
        "product_id": pa.array(product_ids),
        "quantity_on_hand": pa.array(on_hand),
        "quantity_reserved": pa.array(reserved),
        "last_recount_at": recount,
    })


def gen_replenishment_chunk(chunk_start: int, chunk_size: int) -> pa.Table:
    n = min(chunk_size, N_REPLENISHMENT - chunk_start)
    ids = np.arange(chunk_start + 1, chunk_start + n + 1, dtype=np.int32)
    store_ids = rng.integers(1, N_STORES + 1, size=n, dtype=np.int32)
    supplier_ids = rng.integers(1, N_SUPPLIERS + 1, size=n, dtype=np.int32)
    product_ids = rng.integers(1, N_PRODUCTS + 1, size=n, dtype=np.int32)
    qty = rng.integers(10, 500, size=n, dtype=np.int32)
    status_choices = np.array(["pending", "shipped", "received", "cancelled"])
    statuses = status_choices[rng.integers(0, 4, size=n)]
    ordered = random_timestamps(date(2022, 1, 1), date(2025, 12, 31), n)
    # Vectorized received_at: ordered + 1-29 days, only for "received" status
    ordered_us = ordered.to_numpy().astype(np.int64)  # microseconds
    day_offsets_us = rng.integers(1, 30, size=n).astype(np.int64) * 86_400_000_000
    received_us = ordered_us + day_offsets_us
    is_received = (statuses == "received")
    # Build received array: valid timestamps for received, null for others
    received_arr = pa.array(
        np.where(is_received, received_us, 0).astype(np.int64),
        type=pa.timestamp("us"),
        mask=~is_received,
    )
    return pa.table({
        "order_id": pa.array(ids),
        "store_id": pa.array(store_ids),
        "supplier_id": pa.array(supplier_ids),
        "product_id": pa.array(product_ids),
        "quantity": pa.array(qty),
        "status": pa.array(statuses, type=pa.string()),
        "ordered_at": ordered,
        "received_at": received_arr,
    })


def gen_customers_chunk(chunk_start: int, chunk_size: int) -> pa.Table:
    n = min(chunk_size, N_CUSTOMERS - chunk_start)
    ids = np.arange(chunk_start + 1, chunk_start + n + 1, dtype=np.int32)
    first = _FIRST_NAMES_ARR[rng.integers(0, len(_FIRST_NAMES_ARR), size=n)]
    last = _LAST_NAMES_ARR[rng.integers(0, len(_LAST_NAMES_ARR), size=n)]
    domains = _DOMAINS_ARR[rng.integers(0, len(_DOMAINS_ARR), size=n)]
    emails = np.char.add(np.char.add(np.array([f"user{i}" for i in ids]), "@"), domains)
    # Phone as numeric string: fast
    phones = np.array([f"+1-{d:010d}" for d in rng.integers(1_000_000_000, 9_999_999_999, size=n)])
    countries = rng.choice(COUNTRIES, n)
    cities = _CITIES_ARR[rng.integers(0, len(_CITIES_ARR), size=n)]
    registered = random_timestamps(date(2018, 1, 1), date(2025, 12, 31), n)
    return pa.table({
        "customer_id": pa.array(ids),
        "first_name": pa.array(first, type=pa.string()),
        "last_name": pa.array(last, type=pa.string()),
        "email": pa.array(emails, type=pa.string()),
        "phone": pa.array(phones, type=pa.string()),
        "country": pa.array(countries, type=pa.string()),
        "city": pa.array(cities, type=pa.string()),
        "registered_at": registered,
    })


def gen_loyalty_chunk(chunk_start: int, chunk_size: int) -> pa.Table:
    n = min(chunk_size, N_LOYALTY - chunk_start)
    ids = np.arange(chunk_start + 1, chunk_start + n + 1, dtype=np.int32)
    # Customer IDs: first N_LOYALTY customers get loyalty
    customer_ids = np.arange(chunk_start + 1, chunk_start + n + 1, dtype=np.int32)
    _tiers_arr = np.array(LOYALTY_TIERS)
    tiers = _tiers_arr[rng.choice(4, size=n, p=[0.4, 0.3, 0.2, 0.1])]
    points = rng.integers(0, 50000, size=n, dtype=np.int32)
    enrolled = random_timestamps(date(2019, 1, 1), date(2025, 12, 31), n)
    return pa.table({
        "loyalty_id": pa.array(ids),
        "customer_id": pa.array(customer_ids),
        "tier": pa.array(tiers, type=pa.string()),
        "points_balance": pa.array(points),
        "enrolled_at": enrolled,
    })


_CURRENCIES_ARR = np.array(CURRENCIES)
_CHANNELS_ARR = np.array(CHANNELS)
_PAYMENT_METHODS_ARR = np.array(PAYMENT_METHODS)


def gen_orders_chunk(chunk_start: int, chunk_size: int) -> pa.Table:
    n = min(chunk_size, N_ORDERS - chunk_start)
    ids = np.arange(chunk_start + 1, chunk_start + n + 1, dtype=np.int64)
    store_ids = rng.integers(1, N_STORES + 1, size=n, dtype=np.int32)
    customer_ids = rng.integers(1, N_CUSTOMERS + 1, size=n, dtype=np.int32)
    employee_ids = rng.integers(1, N_EMPLOYEES + 1, size=n, dtype=np.int32)
    order_dates = random_dates(ORDER_START, ORDER_END, n)
    totals = np.round(rng.uniform(5.0, 2000.0, size=n), 2).astype(np.float64)
    currencies = _CURRENCIES_ARR[rng.integers(0, len(_CURRENCIES_ARR), size=n)]
    channels = _CHANNELS_ARR[rng.integers(0, len(_CHANNELS_ARR), size=n)]
    return pa.table({
        "order_id": pa.array(ids),
        "store_id": pa.array(store_ids),
        "customer_id": pa.array(customer_ids),
        "employee_id": pa.array(employee_ids),
        "order_date": order_dates,
        "total_amount": pa.array(totals),
        "currency": pa.array(currencies, type=pa.string()),
        "channel": pa.array(channels, type=pa.string()),
    })


def gen_order_lines_chunk(chunk_start: int, chunk_size: int) -> pa.Table:
    n = min(chunk_size, N_ORDER_LINES - chunk_start)
    ids = np.arange(chunk_start + 1, chunk_start + n + 1, dtype=np.int64)
    # Distribute across orders: ~3 lines per order
    order_ids = rng.integers(1, N_ORDERS + 1, size=n, dtype=np.int64)
    product_ids = rng.integers(1, N_PRODUCTS + 1, size=n, dtype=np.int32)
    qty = rng.integers(1, 10, size=n, dtype=np.int32)
    unit_prices = np.round(rng.uniform(0.99, 499.99, size=n), 2).astype(np.float64)
    discounts = np.round(rng.uniform(0, 50.0, size=n) * (rng.random(n) < 0.3), 2
                         ).astype(np.float64)
    line_totals = np.round(qty * unit_prices - discounts, 2).astype(np.float64)
    # Ensure no negative totals
    line_totals = np.maximum(line_totals, 0.0)
    return pa.table({
        "order_line_id": pa.array(ids),
        "order_id": pa.array(order_ids),
        "product_id": pa.array(product_ids),
        "quantity": pa.array(qty),
        "unit_price": pa.array(unit_prices),
        "discount_amount": pa.array(discounts),
        "line_total": pa.array(line_totals),
    })


def gen_payments_chunk(chunk_start: int, chunk_size: int) -> pa.Table:
    n = min(chunk_size, N_PAYMENTS - chunk_start)
    ids = np.arange(chunk_start + 1, chunk_start + n + 1, dtype=np.int64)
    # 1:1 with orders
    order_ids = np.arange(chunk_start + 1, chunk_start + n + 1, dtype=np.int64)
    # Weighted: 20% cash, 60% card, 20% mobile
    method_idx = rng.choice(3, size=n, p=[0.2, 0.6, 0.2])
    methods = _PAYMENT_METHODS_ARR[method_idx]
    amounts = np.round(rng.uniform(5.0, 2000.0, size=n), 2).astype(np.float64)
    currencies = _CURRENCIES_ARR[rng.integers(0, len(_CURRENCIES_ARR), size=n)]
    paid_at = random_timestamps(ORDER_START, ORDER_END, n)
    return pa.table({
        "payment_id": pa.array(ids),
        "order_id": pa.array(order_ids),
        "method": pa.array(methods, type=pa.string()),
        "amount": pa.array(amounts),
        "currency": pa.array(currencies, type=pa.string()),
        "paid_at": paid_at,
    })


# ---------------------------------------------------------------------------
# Chunked write helper
# ---------------------------------------------------------------------------


def write_chunked(ns: str, table_name: str, total: int, gen_fn, schema: pa.Schema):
    """Generate and write a large table, targeting ~512 MB Parquet files on disk."""
    t0 = time.time()

    # 1. Generate first chunk and calibrate compression
    first_chunk = gen_fn(0, CHUNK_SIZE)
    generated = first_chunk.num_rows
    bytes_per_row = calibrate_parquet_bytes_per_row(first_chunk)
    target_rows = int(TARGET_DISK_SIZE / bytes_per_row)
    est_mem_mb = int(target_rows * first_chunk.nbytes / first_chunk.num_rows / 1024 / 1024)
    print(f"    Calibration: {bytes_per_row:.1f} disk bytes/row, "
          f"target {target_rows:,} rows/file, ~{est_mem_mb} MB memory/flush")

    # 2. Create Iceberg table (bin-packer bypass ensures one file per append)
    iceberg_table = create_table(ns, table_name, schema)

    # 3. Accumulate and flush loop
    written = 0
    pending: list[pa.Table] = [first_chunk]
    pending_rows = first_chunk.num_rows
    calibrated = False

    while True:
        # Generate more chunks until we have enough for a file (or exhaust total)
        while pending_rows < target_rows and generated < total:
            chunk = gen_fn(generated, CHUNK_SIZE)
            generated += chunk.num_rows
            pending.append(chunk)
            pending_rows += chunk.num_rows

        # Flush: concat, free pending list, then append
        combined = pa.concat_tables(pending)
        pending = []
        pending_rows = 0
        iceberg_table.append(combined)
        written += combined.num_rows
        del combined

        # Refine calibration after first real write
        if not calibrated:
            files = iceberg_table.inspect.data_files().to_pylist()
            if files:
                actual_size = files[-1]["file_size_in_bytes"]
                actual_rows = files[-1]["record_count"]
                if actual_size > 0 and actual_rows > 0:
                    bytes_per_row = actual_size / actual_rows
                    target_rows = int(TARGET_DISK_SIZE / bytes_per_row)
                    print(f"    Refined: {bytes_per_row:.1f} disk bytes/row, "
                          f"target {target_rows:,} rows/file")
                calibrated = True

        pct = written * 100 // total
        print(f"    {pct:3d}% — {written:>14,}/{total:,} rows ({elapsed(t0)})")

        if generated >= total:
            break

    print(f"  Done: {table_name} — {written:,} rows in {elapsed(t0)}")


def verify_file_sizes():
    """Log file sizes for all tables and warn if non-final files deviate from target."""
    print(f"\n{'=' * 60}")
    print("File size verification")
    print(f"{'=' * 60}")
    target_mb = TARGET_DISK_SIZE / 1024 / 1024
    for ns in NAMESPACES.values():
        try:
            tables = catalog.list_tables(ns)
        except Exception:
            continue
        for _, tbl_name in tables:
            full = f"{ns}.{tbl_name}"
            try:
                tbl = catalog.load_table(full)
                files = tbl.inspect.data_files().to_pylist()
            except Exception:
                continue
            if not files:
                continue
            sizes = [f["file_size_in_bytes"] for f in files]
            avg_mb = sum(sizes) / len(sizes) / 1024 / 1024
            min_mb = min(sizes) / 1024 / 1024
            max_mb = max(sizes) / 1024 / 1024
            print(f"\n  {full}: {len(sizes)} file(s), "
                  f"avg {avg_mb:.1f} MB, min {min_mb:.1f} MB, max {max_mb:.1f} MB")
            # Warn if any non-final file is outside 80-120% of target
            if len(sizes) > 1:
                non_final = sorted(sizes, reverse=True)[:-1]  # exclude smallest (likely last)
                for i, s in enumerate(non_final):
                    s_mb = s / 1024 / 1024
                    if s_mb < target_mb * 0.8 or s_mb > target_mb * 1.2:
                        print(f"    WARNING: file {i} is {s_mb:.1f} MB "
                              f"(outside 80-120% of {target_mb:.0f} MB target)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    t_start = time.time()
    print("=" * 60)
    print("Generating synthetic retail data")
    print("=" * 60)

    # Ensure all namespaces exist
    print("\nCreating namespaces...")
    for ns in NAMESPACES.values():
        ensure_namespace(ns)

    # -----------------------------------------------------------------------
    # retail_catalog.products
    # -----------------------------------------------------------------------
    ns = "retail_catalog__products"
    print(f"\n--- {ns} ---")

    t = time.time()
    tbl = gen_categories()
    it = create_table(ns, "categories", tbl.schema)
    it.append(tbl)
    print(f"  categories: {tbl.num_rows:,} rows ({elapsed(t)})")

    t = time.time()
    tbl = gen_brands()
    it = create_table(ns, "brands", tbl.schema)
    it.append(tbl)
    print(f"  brands: {tbl.num_rows:,} rows ({elapsed(t)})")

    t = time.time()
    tbl = gen_suppliers()
    it = create_table(ns, "suppliers", tbl.schema)
    it.append(tbl)
    print(f"  suppliers: {tbl.num_rows:,} rows ({elapsed(t)})")

    t = time.time()
    tbl = gen_products()
    it = create_table(ns, "products", tbl.schema)
    it.append(tbl)
    print(f"  products: {tbl.num_rows:,} rows ({elapsed(t)})")

    # -----------------------------------------------------------------------
    # retail_catalog.pricing
    # -----------------------------------------------------------------------
    ns = "retail_catalog__pricing"
    print(f"\n--- {ns} ---")

    t = time.time()
    tbl = gen_price_lists()
    it = create_table(ns, "price_lists", tbl.schema)
    it.append(tbl)
    print(f"  price_lists: {tbl.num_rows:,} rows ({elapsed(t)})")

    t = time.time()
    tbl = gen_promotions()
    it = create_table(ns, "promotions", tbl.schema)
    it.append(tbl)
    print(f"  promotions: {tbl.num_rows:,} rows ({elapsed(t)})")

    t = time.time()
    tbl = gen_promotion_products()
    it = create_table(ns, "promotion_products", tbl.schema)
    it.append(tbl)
    print(f"  promotion_products: {tbl.num_rows:,} rows ({elapsed(t)})")

    # -----------------------------------------------------------------------
    # retail_ops.stores
    # -----------------------------------------------------------------------
    ns = "retail_ops__stores"
    print(f"\n--- {ns} ---")

    t = time.time()
    tbl = gen_regions()
    it = create_table(ns, "regions", tbl.schema)
    it.append(tbl)
    print(f"  regions: {tbl.num_rows:,} rows ({elapsed(t)})")

    t = time.time()
    tbl = gen_stores()
    it = create_table(ns, "stores", tbl.schema)
    it.append(tbl)
    print(f"  stores: {tbl.num_rows:,} rows ({elapsed(t)})")

    t = time.time()
    tbl = gen_employees()
    it = create_table(ns, "employees", tbl.schema)
    it.append(tbl)
    print(f"  employees: {tbl.num_rows:,} rows ({elapsed(t)})")

    # -----------------------------------------------------------------------
    # retail_ops.inventory (chunked — large tables)
    # -----------------------------------------------------------------------
    ns = "retail_ops__inventory"
    print(f"\n--- {ns} ---")

    # Get schema from a small sample
    sample = gen_stock_levels_chunk(0, 1)
    write_chunked(ns, "stock_levels", N_STOCK_LEVELS, gen_stock_levels_chunk,
                  sample.schema)

    sample = gen_replenishment_chunk(0, 1)
    write_chunked(ns, "replenishment_orders", N_REPLENISHMENT,
                  gen_replenishment_chunk, sample.schema)

    # -----------------------------------------------------------------------
    # retail_sales.customers
    # -----------------------------------------------------------------------
    ns = "retail_sales__customers"
    print(f"\n--- {ns} ---")

    sample = gen_customers_chunk(0, 1)
    write_chunked(ns, "customers", N_CUSTOMERS, gen_customers_chunk, sample.schema)

    sample = gen_loyalty_chunk(0, 1)
    write_chunked(ns, "loyalty_accounts", N_LOYALTY, gen_loyalty_chunk, sample.schema)

    # -----------------------------------------------------------------------
    # retail_sales.transactions (chunked — very large tables)
    # -----------------------------------------------------------------------
    ns = "retail_sales__transactions"
    print(f"\n--- {ns} ---")

    sample = gen_orders_chunk(0, 1)
    write_chunked(ns, "orders", N_ORDERS, gen_orders_chunk, sample.schema)

    sample = gen_order_lines_chunk(0, 1)
    write_chunked(ns, "order_lines", N_ORDER_LINES, gen_order_lines_chunk,
                  sample.schema)

    sample = gen_payments_chunk(0, 1)
    write_chunked(ns, "payments", N_PAYMENTS, gen_payments_chunk, sample.schema)

    # -----------------------------------------------------------------------
    verify_file_sizes()

    print(f"\n{'=' * 60}")
    print(f"All done in {elapsed(t_start)}")
    print(f"{'=' * 60}")
    print("\nRestart the backend to register tables in the catalog.")


if __name__ == "__main__":
    main()
