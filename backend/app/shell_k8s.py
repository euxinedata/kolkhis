"""Dynamic per-org shell pod provisioning via Kubernetes API."""

import asyncio
import logging

from kubernetes import client, config as k8s_config
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import SHELL_NAMESPACE, SHELL_IMAGE
from app.models import Organization, ShellProvision

log = logging.getLogger(__name__)

# Lazy-initialized K8s clients
_core_v1: client.CoreV1Api | None = None
_apps_v1: client.AppsV1Api | None = None


def _init_k8s():
    global _core_v1, _apps_v1
    if _core_v1 is not None:
        return
    k8s_config.load_incluster_config()
    _core_v1 = client.CoreV1Api()
    _apps_v1 = client.AppsV1Api()


def _resource_names(org_id: str) -> dict[str, str]:
    """Generate predictable resource names for an org's shell pod."""
    return {
        "pv": f"shell-homes-{org_id}",
        "pvc": f"shell-homes-{org_id}",
        "deployment": f"shell-{org_id}",
        "service": f"shell-{org_id}",
    }


def _record(db_records: list, org_id: str, action: str, rtype: str, rname: str, status: str, error: str | None = None):
    db_records.append(ShellProvision(
        org_id=org_id, action=action, resource_type=rtype,
        resource_name=rname, status=status, error=error,
    ))


def _create_pv(org_id: str, names: dict) -> None:
    """Create a PV pointing to /mnt/data/homes/{org_id}."""
    pv = client.V1PersistentVolume(
        api_version="v1",
        kind="PersistentVolume",
        metadata=client.V1ObjectMeta(
            name=names["pv"],
            labels={"app": f"shell-{org_id}", "managed-by": "kolkhis"},
        ),
        spec=client.V1PersistentVolumeSpec(
            capacity={"storage": "5Gi"},
            access_modes=["ReadWriteOnce"],
            host_path=client.V1HostPathVolumeSource(path=f"/mnt/data/homes/{org_id}"),
            storage_class_name="manual",
            persistent_volume_reclaim_policy="Retain",
        ),
    )
    _core_v1.create_persistent_volume(body=pv)


def _create_pvc(org_id: str, names: dict) -> None:
    """Create a PVC in the kolkhis namespace bound to the org's PV."""
    pvc = client.V1PersistentVolumeClaim(
        api_version="v1",
        kind="PersistentVolumeClaim",
        metadata=client.V1ObjectMeta(
            name=names["pvc"],
            namespace=SHELL_NAMESPACE,
            labels={"app": f"shell-{org_id}", "managed-by": "kolkhis"},
        ),
        spec=client.V1PersistentVolumeClaimSpec(
            access_modes=["ReadWriteOnce"],
            resources=client.V1VolumeResourceRequirements(
                requests={"storage": "5Gi"},
            ),
            storage_class_name="manual",
            volume_name=names["pv"],
        ),
    )
    _core_v1.create_namespaced_persistent_volume_claim(namespace=SHELL_NAMESPACE, body=pvc)


def _create_deployment(org_id: str, names: dict) -> None:
    """Create a shell Deployment for the org."""
    deployment = client.V1Deployment(
        api_version="apps/v1",
        kind="Deployment",
        metadata=client.V1ObjectMeta(
            name=names["deployment"],
            namespace=SHELL_NAMESPACE,
            labels={"app": f"shell-{org_id}", "managed-by": "kolkhis"},
        ),
        spec=client.V1DeploymentSpec(
            replicas=1,
            selector=client.V1LabelSelector(
                match_labels={"app": f"shell-{org_id}"},
            ),
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(
                    labels={"app": f"shell-{org_id}", "managed-by": "kolkhis"},
                ),
                spec=client.V1PodSpec(
                    containers=[
                        client.V1Container(
                            name="shell",
                            image=SHELL_IMAGE,
                            ports=[client.V1ContainerPort(container_port=22)],
                            volume_mounts=[
                                client.V1VolumeMount(
                                    name="homes",
                                    mount_path="/home",
                                ),
                                client.V1VolumeMount(
                                    name="shell-ssh-keys",
                                    mount_path="/etc/shell-ssh",
                                    read_only=True,
                                ),
                            ],
                        ),
                    ],
                    volumes=[
                        client.V1Volume(
                            name="homes",
                            persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                                claim_name=names["pvc"],
                            ),
                        ),
                        client.V1Volume(
                            name="shell-ssh-keys",
                            secret=client.V1SecretVolumeSource(
                                secret_name="shell-ssh-keys",
                                default_mode=0o400,
                            ),
                        ),
                    ],
                ),
            ),
        ),
    )
    _apps_v1.create_namespaced_deployment(namespace=SHELL_NAMESPACE, body=deployment)


def _create_service(org_id: str, names: dict) -> None:
    """Create a ClusterIP Service targeting the org's shell pod."""
    service = client.V1Service(
        api_version="v1",
        kind="Service",
        metadata=client.V1ObjectMeta(
            name=names["service"],
            namespace=SHELL_NAMESPACE,
            labels={"app": f"shell-{org_id}", "managed-by": "kolkhis"},
        ),
        spec=client.V1ServiceSpec(
            selector={"app": f"shell-{org_id}"},
            ports=[client.V1ServicePort(port=22, target_port=22)],
        ),
    )
    _core_v1.create_namespaced_service(namespace=SHELL_NAMESPACE, body=service)


def _provision_sync(org_id: str) -> list[ShellProvision]:
    """Synchronous K8s provisioning. Returns audit records."""
    _init_k8s()
    names = _resource_names(org_id)
    records: list[ShellProvision] = []

    steps = [
        ("pv", _create_pv),
        ("pvc", _create_pvc),
        ("deployment", _create_deployment),
        ("service", _create_service),
    ]

    for rtype, create_fn in steps:
        try:
            create_fn(org_id, names)
            _record(records, org_id, "create", rtype, names[rtype], "success")
            log.info("Created %s %s for org %s", rtype, names[rtype], org_id)
        except client.ApiException as e:
            if e.status == 409:
                _record(records, org_id, "create", rtype, names[rtype], "success", "already exists")
                log.info("%s %s already exists for org %s", rtype, names[rtype], org_id)
            else:
                _record(records, org_id, "create", rtype, names[rtype], "failed", str(e.reason))
                log.error("Failed to create %s %s for org %s: %s", rtype, names[rtype], org_id, e.reason)
                raise

    return records


async def provision_shell_pod(org_id: str, db: AsyncSession) -> str:
    """Provision K8s resources for an org's shell pod. Returns the service name."""
    names = _resource_names(org_id)
    service_name = names["service"]

    records = await asyncio.to_thread(_provision_sync, org_id)

    for record in records:
        db.add(record)

    # Update org with service name
    org = await db.get(Organization, org_id)
    if org:
        org.shell_service_name = service_name

    await db.commit()
    log.info("Shell pod provisioned for org %s at %s", org_id, service_name)
    return service_name


def shell_host_for_org(org: Organization) -> str:
    """Return the SSH host for an org's shell pod."""
    if org.shell_service_name:
        return org.shell_service_name
    return f"shell-{org.id}"
