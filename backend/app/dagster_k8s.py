"""Dynamic per-org Dagster code location provisioning via Kubernetes API."""

import asyncio
import base64
import logging

import yaml
from kubernetes import client, config as k8s_config

from app.auth import make_service_token
from app.config import (
    DAGSTER_NAMESPACE, DAGSTER_IMAGE, DAGSTER_RELOAD_TOKEN,
    SHELL_BACKEND_URL,
    GITEA_SHELL_URL, GITEA_ADMIN_USER, GITEA_ADMIN_PASSWORD,
)

log = logging.getLogger(__name__)

_core_v1: client.CoreV1Api | None = None
_apps_v1: client.AppsV1Api | None = None

WORKSPACE_CONFIGMAP = "dagster-workspace"


def _init_k8s():
    global _core_v1, _apps_v1
    if _core_v1 is not None:
        return
    k8s_config.load_incluster_config()
    _core_v1 = client.CoreV1Api()
    _apps_v1 = client.AppsV1Api()


def _resource_names(org_id: str) -> dict[str, str]:
    return {
        "secret": f"dagster-code-{org_id}",
        "deployment": f"dagster-code-{org_id}",
        "service": f"dagster-code-{org_id}",
    }


def _b64(value: str) -> str:
    return base64.b64encode(value.encode()).decode()


def _create_secret(org_id: str, names: dict) -> None:
    secret = client.V1Secret(
        api_version="v1",
        kind="Secret",
        metadata=client.V1ObjectMeta(
            name=names["secret"],
            namespace=DAGSTER_NAMESPACE,
            labels={"app": "dagster-code", "org-id": org_id, "managed-by": "kolkhis"},
        ),
        data={
            "DAGSTER_ORG_ID": _b64(org_id),
            "DAGSTER_REPO": _b64("warehouse"),
            "KOLKHIS_AUTH_TOKEN": _b64(make_service_token(org_id)),
            "KOLKHIS_BACKEND_URL": _b64(SHELL_BACKEND_URL),
            "DAGSTER_RELOAD_TOKEN": _b64(DAGSTER_RELOAD_TOKEN),
            "GITEA_SHELL_URL": _b64(GITEA_SHELL_URL),
            "GITEA_ADMIN_USER": _b64(GITEA_ADMIN_USER),
            "GITEA_ADMIN_PASSWORD": _b64(GITEA_ADMIN_PASSWORD),
        },
    )
    _core_v1.create_namespaced_secret(namespace=DAGSTER_NAMESPACE, body=secret)


def _create_deployment(org_id: str, names: dict) -> None:
    deployment = client.V1Deployment(
        api_version="apps/v1",
        kind="Deployment",
        metadata=client.V1ObjectMeta(
            name=names["deployment"],
            namespace=DAGSTER_NAMESPACE,
            labels={"app": "dagster-code", "org-id": org_id, "managed-by": "kolkhis"},
        ),
        spec=client.V1DeploymentSpec(
            replicas=1,
            selector=client.V1LabelSelector(
                match_labels={"app": "dagster-code", "org-id": org_id},
            ),
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(
                    labels={"app": "dagster-code", "org-id": org_id, "managed-by": "kolkhis"},
                ),
                spec=client.V1PodSpec(
                    containers=[
                        client.V1Container(
                            name="dagster-code",
                            image=DAGSTER_IMAGE,
                            ports=[
                                client.V1ContainerPort(container_port=3030, name="grpc"),
                                client.V1ContainerPort(container_port=3031, name="reload"),
                            ],
                            env_from=[
                                client.V1EnvFromSource(
                                    secret_ref=client.V1SecretEnvSource(name=names["secret"]),
                                ),
                            ],
                        ),
                    ],
                ),
            ),
        ),
    )
    _apps_v1.create_namespaced_deployment(namespace=DAGSTER_NAMESPACE, body=deployment)


def _create_service(org_id: str, names: dict) -> None:
    service = client.V1Service(
        api_version="v1",
        kind="Service",
        metadata=client.V1ObjectMeta(
            name=names["service"],
            namespace=DAGSTER_NAMESPACE,
            labels={"app": "dagster-code", "org-id": org_id, "managed-by": "kolkhis"},
        ),
        spec=client.V1ServiceSpec(
            selector={"app": "dagster-code", "org-id": org_id},
            ports=[
                client.V1ServicePort(port=3030, target_port=3030, name="grpc"),
                client.V1ServicePort(port=3031, target_port=3031, name="reload"),
            ],
        ),
    )
    _core_v1.create_namespaced_service(namespace=DAGSTER_NAMESPACE, body=service)


def _update_workspace_configmap(org_id: str, names: dict, retries: int = 3) -> None:
    """Add a grpc_server entry to the shared workspace ConfigMap.

    Uses retry on 409 conflict (optimistic concurrency via resourceVersion).
    """
    service_host = f"{names['service']}.{DAGSTER_NAMESPACE}.svc.cluster.local"
    location_name = f"org-{org_id}"

    for attempt in range(retries):
        # Read or create the ConfigMap
        try:
            cm = _core_v1.read_namespaced_config_map(
                name=WORKSPACE_CONFIGMAP, namespace=DAGSTER_NAMESPACE,
            )
        except client.ApiException as e:
            if e.status == 404:
                cm = client.V1ConfigMap(
                    api_version="v1",
                    kind="ConfigMap",
                    metadata=client.V1ObjectMeta(
                        name=WORKSPACE_CONFIGMAP,
                        namespace=DAGSTER_NAMESPACE,
                        labels={"managed-by": "kolkhis"},
                    ),
                    data={"workspace.yaml": "load_from: []\n"},
                )
                _core_v1.create_namespaced_config_map(namespace=DAGSTER_NAMESPACE, body=cm)
            else:
                raise

        workspace = yaml.safe_load(cm.data.get("workspace.yaml", "load_from: []"))
        if not workspace or "load_from" not in workspace:
            workspace = {"load_from": []}

        # Check if entry already exists
        for entry in workspace["load_from"]:
            if isinstance(entry, dict) and "grpc_server" in entry:
                if entry["grpc_server"].get("location_name") == location_name:
                    log.info("Workspace entry for %s already exists", location_name)
                    return

        workspace["load_from"].append({
            "grpc_server": {
                "host": service_host,
                "port": 3030,
                "location_name": location_name,
            }
        })

        cm.data["workspace.yaml"] = yaml.dump(workspace, default_flow_style=False)
        try:
            _core_v1.replace_namespaced_config_map(
                name=WORKSPACE_CONFIGMAP, namespace=DAGSTER_NAMESPACE, body=cm,
            )
            log.info("Updated workspace ConfigMap with %s", location_name)
            return
        except client.ApiException as e:
            if e.status == 409 and attempt < retries - 1:
                log.warning("ConfigMap conflict, retrying (%d/%d)", attempt + 1, retries)
                continue
            raise


def _provision_sync(org_id: str) -> None:
    _init_k8s()
    names = _resource_names(org_id)

    steps = [
        ("secret", _create_secret),
        ("deployment", _create_deployment),
        ("service", _create_service),
    ]

    for rtype, create_fn in steps:
        try:
            create_fn(org_id, names)
            log.info("Created dagster %s for org %s", rtype, org_id)
        except client.ApiException as e:
            if e.status == 409:
                log.info("Dagster %s already exists for org %s", rtype, org_id)
            else:
                log.error("Failed to create dagster %s for org %s: %s", rtype, org_id, e.reason)
                raise

    # ConfigMap update is not a create — handle separately
    _update_workspace_configmap(org_id, names)


async def provision_dagster_pod(org_id: str) -> str:
    names = _resource_names(org_id)
    await asyncio.to_thread(_provision_sync, org_id)
    service_name = names["service"]
    log.info("Dagster pod provisioned for org %s at %s", org_id, service_name)
    return service_name


def dagster_reload_url_for_org(org_id: str) -> str:
    names = _resource_names(org_id)
    return f"http://{names['service']}.{DAGSTER_NAMESPACE}.svc.cluster.local:3031"
