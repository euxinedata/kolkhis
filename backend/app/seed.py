import pycountry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Country


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
