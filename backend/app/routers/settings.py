from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.database import get_db
from app.models import UserSettings

router = APIRouter(prefix="/api/settings")


class UpdateSettings(BaseModel):
    idle_timeout: int
    worker_size: str | None = None


@router.get("")
async def get_settings(
    user: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    user_id = int(user["sub"])
    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == user_id)
    )
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = UserSettings(user_id=user_id)
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return {"idle_timeout": settings.idle_timeout, "worker_size": settings.worker_size}


@router.put("")
async def update_settings(
    body: UpdateSettings,
    user: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    user_id = int(user["sub"])
    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == user_id)
    )
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = UserSettings(user_id=user_id, idle_timeout=body.idle_timeout)
        if body.worker_size is not None:
            settings.worker_size = body.worker_size
        db.add(settings)
    else:
        settings.idle_timeout = body.idle_timeout
        if body.worker_size is not None:
            settings.worker_size = body.worker_size
    await db.commit()
    await db.refresh(settings)
    return {"idle_timeout": settings.idle_timeout, "worker_size": settings.worker_size}
