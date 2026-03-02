from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ServerTypeRate, UsageEvent


async def compute_billing_summary(
    session: AsyncSession,
    user_id: int,
    period_start: datetime,
    period_end: datetime,
) -> dict:
    """Compute billing summary for a user over a time period."""
    # Load usage events in period
    result = await session.execute(
        select(UsageEvent)
        .where(
            UsageEvent.user_id == user_id,
            UsageEvent.event_type.in_(["compute_start", "compute_stop"]),
            UsageEvent.created_at >= period_start,
            UsageEvent.created_at < period_end,
        )
        .order_by(UsageEvent.created_at)
    )
    events = result.scalars().all()

    # Also load any compute_start from before the period that hasn't stopped yet
    # (VMs that were started before period_start and are still running or stopped during period)
    result = await session.execute(
        select(UsageEvent)
        .where(
            UsageEvent.user_id == user_id,
            UsageEvent.event_type == "compute_start",
            UsageEvent.created_at < period_start,
        )
        .order_by(UsageEvent.created_at)
    )
    pre_period_starts = result.scalars().all()

    # Pair start/stop by worker_vm_id
    starts: dict[int, datetime] = {}
    stops: dict[int, datetime] = {}

    for evt in pre_period_starts:
        if evt.worker_vm_id is not None:
            starts[evt.worker_vm_id] = evt.created_at

    for evt in events:
        if evt.worker_vm_id is None:
            continue
        if evt.event_type == "compute_start":
            starts[evt.worker_vm_id] = evt.created_at
        elif evt.event_type == "compute_stop":
            stops[evt.worker_vm_id] = evt.created_at

    # Remove pre-period starts that stopped before the period
    result = await session.execute(
        select(UsageEvent)
        .where(
            UsageEvent.user_id == user_id,
            UsageEvent.event_type == "compute_stop",
            UsageEvent.created_at < period_start,
        )
    )
    pre_period_stops = result.scalars().all()
    for evt in pre_period_stops:
        if evt.worker_vm_id is not None and evt.worker_vm_id in starts:
            if evt.worker_vm_id not in stops:
                # Stopped before period, remove from tracking
                del starts[evt.worker_vm_id]

    # Load rates
    result = await session.execute(select(ServerTypeRate))
    rates = {r.server_type: r for r in result.scalars().all()}

    # Build server_type -> event mapping for cost calculation
    server_types: dict[int, str] = {}
    for evt in pre_period_starts:
        if evt.worker_vm_id is not None:
            server_types[evt.worker_vm_id] = evt.server_type or ""
    for evt in events:
        if evt.worker_vm_id is not None and evt.server_type:
            server_types[evt.worker_vm_id] = evt.server_type

    now = datetime.utcnow()
    effective_end = min(now, period_end)

    # Compute seconds per server type
    type_seconds: dict[str, int] = {}
    for vm_id, start_time in starts.items():
        effective_start = max(start_time, period_start)
        end_time = stops.get(vm_id, effective_end)
        end_time = min(end_time, period_end)
        seconds = max(0, int((end_time - effective_start).total_seconds()))
        st = server_types.get(vm_id, "")
        type_seconds[st] = type_seconds.get(st, 0) + seconds

    # Compute costs
    line_items = []
    total_seconds = 0
    total_cost_cents = 0
    for st, seconds in sorted(type_seconds.items()):
        rate = rates.get(st)
        hourly_rate = rate.hourly_rate_cents if rate else 0
        display_name = rate.display_name if rate else st
        cost_cents = int(seconds * hourly_rate / 3600)
        line_items.append({
            "server_type": st,
            "display_name": display_name,
            "seconds": seconds,
            "hours": round(seconds / 3600, 2),
            "hourly_rate_cents": hourly_rate,
            "cost_cents": cost_cents,
        })
        total_seconds += seconds
        total_cost_cents += cost_cents

    return {
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "compute_seconds": total_seconds,
        "compute_cost_cents": total_cost_cents,
        "storage_cost_cents": 0,
        "total_cost_cents": total_cost_cents,
        "line_items": line_items,
    }
