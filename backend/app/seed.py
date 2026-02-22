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

        # Create a schema for each Iceberg namespace (if not "public")
        if ns_name == "public":
            schema_obj = public_schema
        else:
            result = await session.execute(
                select(Schema).where(
                    Schema.database_id == db.id, Schema.name == ns_name
                )
            )
            schema_obj = result.scalar()
            if schema_obj is None:
                schema_obj = Schema(database_id=db.id, name=ns_name)
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
