import logging

import pycountry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CatalogObject, Country, Database, Schema
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

    # Scan Iceberg namespaces and register tables
    for ns_tuple in catalog.list_namespaces():
        ns_name = ns_tuple[0]

        # Parse namespace: "db__schema" → (db, schema) or plain → (kolkhis, ns)
        if "__" in ns_name:
            target_db_name, target_schema_name = ns_name.split("__", 1)
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
            tbl_name = tbl_tuple[1]
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
