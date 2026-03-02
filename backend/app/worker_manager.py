import asyncio
import logging
from datetime import datetime, timedelta

import httpx
from hcloud import Client
from hcloud.images import Image
from hcloud.networks import Network
from hcloud.server_types import ServerType
from hcloud.locations import Location
from hcloud.ssh_keys import SSHKey
from sqlalchemy import delete, or_, select, update

from app.config import (
    HCLOUD_TOKEN,
    S3_ACCESS_KEY,
    S3_ENDPOINT,
    S3_REGION,
    S3_SECRET_KEY,
    S3_RESULTS_BUCKET,
    WORKER_AUTH_TOKEN,
    WORKER_IDLE_TIMEOUT,
    WORKER_LOCATION,
    WORKER_MODE,
    WORKER_NETWORK_ID,
    WORKER_SERVER_TYPE,
    WORKER_SNAPSHOT_ID,
)
from app.database import async_session
from app.models import UserSettings, WorkerVM

logger = logging.getLogger(__name__)

_hcloud: Client | None = None


def _get_hcloud() -> Client:
    global _hcloud
    if _hcloud is None:
        _hcloud = Client(token=HCLOUD_TOKEN)
    return _hcloud


def _cloud_init_user_data() -> str:
    return f"""#cloud-config
write_files:
  - path: /etc/kolkhis-worker/env
    content: |
      WORKER_AUTH_TOKEN={WORKER_AUTH_TOKEN}
      S3_ENDPOINT={S3_ENDPOINT}
      S3_ACCESS_KEY={S3_ACCESS_KEY}
      S3_SECRET_KEY={S3_SECRET_KEY}
      S3_REGION={S3_REGION}
runcmd:
  - systemctl start kolkhis-worker
"""


async def ensure_worker(user_id: int) -> WorkerVM:
    """Return an existing ready/provisioning VM for the user, or provision a new one."""
    async with async_session() as session:
        result = await session.execute(
            select(WorkerVM).where(
                WorkerVM.user_id == user_id,
                WorkerVM.status.in_(["provisioning", "ready"]),
            )
        )
        vm = result.scalar_one_or_none()
        if vm is not None:
            return vm

    # Remove any old destroyed/destroying rows for this user
    async with async_session() as session:
        await session.execute(
            delete(WorkerVM).where(
                WorkerVM.user_id == user_id,
                WorkerVM.status.in_(["destroyed", "destroying"]),
            )
        )
        await session.commit()

    # Provision a new VM
    client = _get_hcloud()
    # Attach all SSH keys from the Hetzner account for debug access
    ssh_keys = await asyncio.to_thread(client.ssh_keys.get_all)
    response = await asyncio.to_thread(
        client.servers.create,
        name=f"worker-{user_id}",
        server_type=ServerType(name=WORKER_SERVER_TYPE),
        image=Image(id=int(WORKER_SNAPSHOT_ID)),
        location=Location(name=WORKER_LOCATION),
        networks=[Network(id=WORKER_NETWORK_ID)],
        ssh_keys=ssh_keys,
        user_data=_cloud_init_user_data(),
    )
    server = response.server
    if WORKER_MODE == "remote":
        # Re-fetch to get private_net populated
        server = await asyncio.to_thread(client.servers.get_by_id, server.id)
        private_ip = server.private_net[0].ip
    else:
        private_ip = server.public_net.ipv4.ip

    vm = WorkerVM(
        user_id=user_id,
        hetzner_server_id=server.id,
        private_ip=private_ip,
        status="provisioning",
    )
    async with async_session() as session:
        session.add(vm)
        await session.commit()
        await session.refresh(vm)
    return vm


async def wait_for_ready(vm_id: int, timeout: float = 300, interval: float = 5):
    """Poll the worker's /health endpoint until it responds 200."""
    async with async_session() as session:
        result = await session.execute(select(WorkerVM).where(WorkerVM.id == vm_id))
        vm = result.scalar_one()

    deadline = asyncio.get_event_loop().time() + timeout
    async with httpx.AsyncClient(timeout=10) as client:
        while asyncio.get_event_loop().time() < deadline:
            try:
                resp = await client.get(f"http://{vm.private_ip}:8080/health")
                if resp.status_code == 200:
                    async with async_session() as session:
                        await session.execute(
                            update(WorkerVM).where(WorkerVM.id == vm_id).values(status="ready")
                        )
                        await session.commit()
                    return
            except httpx.TransportError:
                pass
            await asyncio.sleep(interval)

    raise TimeoutError(f"Worker VM {vm_id} did not become ready within {timeout}s")


async def destroy_worker(vm_id: int):
    """Delete the Hetzner server and mark the DB row as destroyed."""
    async with async_session() as session:
        result = await session.execute(select(WorkerVM).where(WorkerVM.id == vm_id))
        vm = result.scalar_one()

    client = _get_hcloud()
    server = await asyncio.to_thread(client.servers.get_by_id, vm.hetzner_server_id)
    if server is not None:
        await asyncio.to_thread(server.delete)

    async with async_session() as session:
        await session.execute(
            update(WorkerVM).where(WorkerVM.id == vm_id).values(status="destroyed")
        )
        await session.commit()


async def idle_reaper():
    """Background loop that destroys idle worker VMs."""
    while True:
        await asyncio.sleep(60)
        try:
            now = datetime.utcnow()
            async with async_session() as session:
                result = await session.execute(
                    select(WorkerVM).where(WorkerVM.status == "ready")
                )
                ready_vms = result.scalars().all()

                # Build a map of user_id -> idle_timeout from UserSettings
                if ready_vms:
                    user_ids = [vm.user_id for vm in ready_vms]
                    settings_result = await session.execute(
                        select(UserSettings).where(UserSettings.user_id.in_(user_ids))
                    )
                    timeout_map = {
                        s.user_id: s.idle_timeout
                        for s in settings_result.scalars().all()
                    }

            idle_vms = []
            for vm in ready_vms:
                timeout = timeout_map.get(vm.user_id, WORKER_IDLE_TIMEOUT)
                cutoff = now - timedelta(seconds=timeout)
                if vm.last_query_at is not None:
                    if vm.last_query_at < cutoff:
                        idle_vms.append(vm)
                elif vm.created_at < cutoff:
                    idle_vms.append(vm)

            for vm in idle_vms:
                logger.info("Reaping idle worker VM %d (user %d)", vm.id, vm.user_id)
                await destroy_worker(vm.id)
        except Exception:
            logger.exception("Error in idle reaper")
