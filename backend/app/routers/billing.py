from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.billing import compute_billing_summary
from app.database import get_db
from app.models import UsageEvent, User

router = APIRouter(prefix="/api/billing")


def _period_boundaries(anchor_day: int, ref: datetime) -> tuple[datetime, datetime]:
    """Compute current billing period start/end from user's signup day-of-month."""
    year, month = ref.year, ref.month
    day = min(anchor_day, 28)  # clamp to avoid month overflow

    period_start = datetime(year, month, day)
    if period_start > ref:
        # We're before the anchor day this month, so period started last month
        if month == 1:
            period_start = datetime(year - 1, 12, day)
        else:
            period_start = datetime(year, month - 1, day)

    # Period end is one month after start
    m = period_start.month + 1
    y = period_start.year
    if m > 12:
        m = 1
        y += 1
    period_end = datetime(y, m, day)

    return period_start, period_end


async def _get_user_anchor(user_id: int, db: AsyncSession) -> int:
    result = await db.execute(select(User.created_at).where(User.id == user_id))
    created_at = result.scalar_one_or_none()
    return created_at.day if created_at else 1


@router.get("/current")
async def billing_current(
    user: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    user_id = int(user["sub"])
    anchor_day = await _get_user_anchor(user_id, db)
    now = datetime.utcnow()
    period_start, period_end = _period_boundaries(anchor_day, now)
    summary = await compute_billing_summary(db, user_id, period_start, period_end)
    return summary


@router.get("/history")
async def billing_history(
    user: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    user_id = int(user["sub"])
    anchor_day = await _get_user_anchor(user_id, db)
    now = datetime.utcnow()

    periods = []
    period_start, period_end = _period_boundaries(anchor_day, now)
    for _ in range(12):
        # Go back one month
        m = period_start.month - 1
        y = period_start.year
        if m < 1:
            m = 12
            y -= 1
        day = min(anchor_day, 28)
        prev_start = datetime(y, m, day)
        prev_end = period_start

        summary = await compute_billing_summary(db, user_id, prev_start, prev_end)
        periods.append(summary)
        period_start = prev_start

    return periods


@router.get("/events")
async def billing_events(
    user: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    user_id = int(user["sub"])
    anchor_day = await _get_user_anchor(user_id, db)
    now = datetime.utcnow()
    period_start, period_end = _period_boundaries(anchor_day, now)

    # Total count
    count_result = await db.execute(
        select(func.count(UsageEvent.id)).where(
            UsageEvent.user_id == user_id,
            UsageEvent.created_at >= period_start,
            UsageEvent.created_at < period_end,
        )
    )
    total = count_result.scalar()

    # Paginated events
    result = await db.execute(
        select(UsageEvent)
        .where(
            UsageEvent.user_id == user_id,
            UsageEvent.created_at >= period_start,
            UsageEvent.created_at < period_end,
        )
        .order_by(UsageEvent.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    events = result.scalars().all()

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "events": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "server_type": e.server_type,
                "worker_vm_id": e.worker_vm_id,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ],
    }
