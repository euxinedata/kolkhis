import logging

import pycountry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Country, ServerTypeRate

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


