from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.config import WORKER_SERVER_TYPE
from app.database import get_db
from app.models import WorkerVM
from app.worker_manager import destroy_worker

router = APIRouter(prefix="/api/workers")


@router.get("")
async def list_workers(
    user: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    user_id = int(user["sub"])
    result = await db.execute(
        select(WorkerVM).where(
            WorkerVM.user_id == user_id,
            WorkerVM.status.in_(["provisioning", "ready"]),
        )
    )
    vms = result.scalars().all()
    return [
        {
            "id": vm.id,
            "status": vm.status,
            "server_type": WORKER_SERVER_TYPE,
            "created_at": vm.created_at.isoformat() if vm.created_at else None,
            "last_query_at": vm.last_query_at.isoformat() if vm.last_query_at else None,
        }
        for vm in vms
    ]


@router.delete("/{vm_id}")
async def delete_worker(
    vm_id: int,
    user: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    user_id = int(user["sub"])
    result = await db.execute(
        select(WorkerVM).where(WorkerVM.id == vm_id, WorkerVM.user_id == user_id)
    )
    vm = result.scalar_one_or_none()
    if vm is None:
        raise HTTPException(status_code=404, detail="Worker not found")
    await destroy_worker(vm.id)
    return {"status": "destroyed"}
