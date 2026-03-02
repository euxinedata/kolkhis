import logging

import pycountry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CatalogObject, Country, Database, Schema, ServerTypeRate
from app.warehouse import catalog

logger = logging.getLogger(__name__)


async def seed_countries(session: AsyncSession) -> None:
    result = await session.execute(select(Country).limit(1))
    if result.scalar() is not None:
        return

    countries = [
        Country(name=c.name, alpha_2=c.alpha_2, alpha_3=c.alpha_3)
        for c in pycountry.countries
    ]
    session.add_all(countries)
    await session.commit()


from decimal import Decimal

_SERVER_TYPE_RATES = [
    {"server_type": "cpx42", "hourly_rate_eur": Decimal("0.0312"), "display_name": "XS Sparrow"},
    {"server_type": "cpx62", "hourly_rate_eur": Decimal("0.0617"), "display_name": "S Dove"},
    {"server_type": "ccx43", "hourly_rate_eur": Decimal("0.1538"), "display_name": "M Falcon"},
    {"server_type": "ccx53", "hourly_rate_eur": Decimal("0.3077"), "display_name": "L Stork"},
    {"server_type": "ccx63", "hourly_rate_eur": Decimal("0.4615"), "display_name": "XL Swan"},
]


async def seed_server_type_rates(session: AsyncSession) -> None:
    result = await session.execute(select(ServerTypeRate))
    existing = {r.server_type: r for r in result.scalars().all()}

    changed = False
    for rate in _SERVER_TYPE_RATES:
        row = existing.get(rate["server_type"])
        if row is None:
            session.add(ServerTypeRate(**rate))
            changed = True
        else:
            if row.display_name != rate["display_name"]:
                row.display_name = rate["display_name"]
                changed = True
            if row.hourly_rate_eur != rate["hourly_rate_eur"]:
                row.hourly_rate_eur = rate["hourly_rate_eur"]
                changed = True

    if changed:
        await session.commit()
        logger.info("Updated server type rates")


async def seed_catalog(session: AsyncSession) -> None:
    """Create default database/schema and register existing Iceberg tables."""
    # Ensure default database exists
    result = await session.execute(
        select(Database).where(Database.name == "kolkhis")
    )
    db = result.scalar()
    if db is None:
        db = Database(name="kolkhis")
        session.add(db)
        await session.flush()

    # Ensure default "public" schema exists
    result = await session.execute(
        select(Schema).where(Schema.database_id == db.id, Schema.name == "public")
    )
    public_schema = result.scalar()
    if public_schema is None:
        public_schema = Schema(database_id=db.id, name="public")
        session.add(public_schema)
        await session.flush()

    # Scan Iceberg namespaces (recursively enumerate nested namespaces)
    all_namespaces = []
    for top_ns in catalog.list_namespaces():
        children = catalog.list_namespaces(top_ns)
        if children:
            all_namespaces.extend(children)
        else:
            all_namespaces.append(top_ns)

    for ns_tuple in all_namespaces:
        ns_name = ".".join(ns_tuple)

        # Parse namespace: "db.schema" → (db, schema) or plain → (kolkhis, ns)
        if "." in ns_name:
            target_db_name, target_schema_name = ns_name.split(".", 1)
        else:
            target_db_name = "kolkhis"
            target_schema_name = ns_name

        # Resolve target database
        if target_db_name == "kolkhis":
            target_db = db
        else:
            result = await session.execute(
                select(Database).where(Database.name == target_db_name)
            )
            target_db = result.scalar()
            if target_db is None:
                target_db = Database(name=target_db_name)
                session.add(target_db)
                await session.flush()

        # Resolve target schema
        if target_db_name == "kolkhis" and target_schema_name == "public":
            schema_obj = public_schema
        else:
            result = await session.execute(
                select(Schema).where(
                    Schema.database_id == target_db.id,
                    Schema.name == target_schema_name,
                )
            )
            schema_obj = result.scalar()
            if schema_obj is None:
                schema_obj = Schema(
                    database_id=target_db.id, name=target_schema_name
                )
                session.add(schema_obj)
                await session.flush()

        # Register tables
        for tbl_tuple in catalog.list_tables(ns_name):
            tbl_name = tbl_tuple[-1]
            iceberg_id = f"{ns_name}.{tbl_name}"

            result = await session.execute(
                select(CatalogObject).where(
                    CatalogObject.schema_id == schema_obj.id,
                    CatalogObject.name == tbl_name,
                )
            )
            if result.scalar() is None:
                session.add(
                    CatalogObject(
                        schema_id=schema_obj.id,
                        name=tbl_name,
                        object_type="table",
                        iceberg_identifier=iceberg_id,
                    )
                )
                logger.info("Registered Iceberg table %s", iceberg_id)

    await session.commit()
