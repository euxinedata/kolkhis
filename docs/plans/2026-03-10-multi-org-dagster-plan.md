# Multi-Org Dagster Code Locations Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Support multiple organizations with isolated Dagster code locations — local mode unchanged, K8s mode provisions per-org dagster-code pods.

**Architecture:** Two-mode system mirroring shell pods. `DAGSTER_MODE=local` keeps the current single-container docker-compose setup. `DAGSTER_MODE=k8s` provisions per-org dagster-code pods (Secret, Deployment, Service) and updates a shared ConfigMap-based `workspace.yaml` so the webserver/daemon discover new code locations via gRPC heartbeat.

**Tech Stack:** FastAPI, kubernetes Python client, Docker, Dagster gRPC

---

### Task 1: Add DAGSTER_MODE config vars to backend

**Files:**
- Modify: `backend/app/config.py:85-87` (dagster config section)

**Step 1: Add the new config vars**

After the existing `DAGSTER_RELOAD_TOKEN` line (line 87), add:

```python
# Dagster code deployment
DAGSTER_CODE_URL = os.environ.get("DAGSTER_CODE_URL", "http://dagster-code:3031")
DAGSTER_RELOAD_TOKEN = os.environ.get("DAGSTER_RELOAD_TOKEN", "")
DAGSTER_MODE = os.environ.get("DAGSTER_MODE", "local")  # "local" or "k8s"
DAGSTER_IMAGE = os.environ.get("DAGSTER_IMAGE", "ghcr.io/euxinedata/kolkhis-dagster:latest")
DAGSTER_NAMESPACE = os.environ.get("DAGSTER_NAMESPACE", SHELL_NAMESPACE)
```

Note: `DAGSTER_NAMESPACE` defaults to `SHELL_NAMESPACE` which is already defined above (line 91). Move the dagster config block **after** the shell config block so the default reference works.

**Step 2: Commit**

```bash
git add backend/app/config.py
git commit -m "Add DAGSTER_MODE, DAGSTER_IMAGE, DAGSTER_NAMESPACE config vars"
```

---

### Task 2: Add self-healing startup to entrypoint.py

**Files:**
- Modify: `docker/dagster/entrypoint.py:198-215` (main function)

The entrypoint already handles `/reload` for both modes. For K8s mode, the pod needs to self-heal on restart by auto-cloning the org's repo and starting gRPC without waiting for a `/reload` call.

**Step 1: Add auto-startup logic in `main()`**

In the `main()` function, after `REPOS_DIR.mkdir(...)` and before registering signal handlers, add:

```python
def main():
    REPOS_DIR.mkdir(parents=True, exist_ok=True)

    # K8s self-healing: auto-clone and start gRPC on boot if DAGSTER_ORG_ID is set
    org_id = os.environ.get("DAGSTER_ORG_ID")
    if org_id:
        repo = os.environ.get("DAGSTER_REPO", "warehouse")
        auth_token = os.environ.get("KOLKHIS_AUTH_TOKEN")
        backend_url = os.environ.get("KOLKHIS_BACKEND_URL")
        log.info("K8s mode: auto-loading org %s repo %s", org_id, repo)
        try:
            result = _do_reload(org_id, repo, auth_token=auth_token, backend_url=backend_url)
            log.info("Auto-load result: %s", result)
        except Exception:
            log.exception("Auto-load failed for org %s (will retry on /reload)", org_id)

    def _shutdown(signum, _frame):
        ...
```

This reads `DAGSTER_ORG_ID`, `DAGSTER_REPO`, `KOLKHIS_AUTH_TOKEN`, and `KOLKHIS_BACKEND_URL` from env vars (injected by the K8s Secret). If clone/gRPC startup fails, the pod still starts the reload API — the backend or a restart can retry.

**Step 2: Commit**

```bash
git add docker/dagster/entrypoint.py
git commit -m "Add self-healing startup for K8s dagster-code pods"
```

---

### Task 3: Create `app/dagster_k8s.py` — K8s provisioning module

**Files:**
- Create: `backend/app/dagster_k8s.py`

This mirrors `app/shell_k8s.py`. On org creation (when `DAGSTER_MODE=k8s`), it provisions:
1. **Secret** `dagster-code-{org_id}` — env vars for the pod
2. **Deployment** `dagster-code-{org_id}` — single replica dagster-code pod
3. **Service** `dagster-code-{org_id}` — ClusterIP on port 3030 (gRPC) and 3031 (reload API)
4. **ConfigMap update** — appends a `grpc_server` entry to the shared `dagster-workspace` ConfigMap

**Step 1: Write the module**

```python
"""Dynamic per-org Dagster code location provisioning via Kubernetes API."""

import asyncio
import base64
import logging

import yaml
from kubernetes import client, config as k8s_config
from sqlalchemy.ext.asyncio import AsyncSession

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


def _create_secret(org_id: str, names: dict) -> None:
    """Create a Secret with env vars for the dagster-code pod."""
    data = {
        "DAGSTER_ORG_ID": base64.b64encode(org_id.encode()).decode(),
        "DAGSTER_REPO": base64.b64encode(b"warehouse").decode(),
        "KOLKHIS_AUTH_TOKEN": base64.b64encode(
            make_service_token(org_id).encode()
        ).decode(),
        "KOLKHIS_BACKEND_URL": base64.b64encode(
            SHELL_BACKEND_URL.encode()
        ).decode(),
        "DAGSTER_RELOAD_TOKEN": base64.b64encode(
            DAGSTER_RELOAD_TOKEN.encode()
        ).decode(),
        "GITEA_SHELL_URL": base64.b64encode(GITEA_SHELL_URL.encode()).decode(),
        "GITEA_ADMIN_USER": base64.b64encode(GITEA_ADMIN_USER.encode()).decode(),
        "GITEA_ADMIN_PASSWORD": base64.b64encode(
            GITEA_ADMIN_PASSWORD.encode()
        ).decode(),
    }
    secret = client.V1Secret(
        api_version="v1",
        kind="Secret",
        metadata=client.V1ObjectMeta(
            name=names["secret"],
            namespace=DAGSTER_NAMESPACE,
            labels={
                "app": "dagster-code",
                "org-id": org_id,
                "managed-by": "kolkhis",
            },
        ),
        data=data,
    )
    _core_v1.create_namespaced_secret(namespace=DAGSTER_NAMESPACE, body=secret)


def _create_deployment(org_id: str, names: dict) -> None:
    """Create a Deployment for the org's dagster-code pod."""
    deployment = client.V1Deployment(
        api_version="apps/v1",
        kind="Deployment",
        metadata=client.V1ObjectMeta(
            name=names["deployment"],
            namespace=DAGSTER_NAMESPACE,
            labels={
                "app": "dagster-code",
                "org-id": org_id,
                "managed-by": "kolkhis",
            },
        ),
        spec=client.V1DeploymentSpec(
            replicas=1,
            selector=client.V1LabelSelector(
                match_labels={
                    "app": "dagster-code",
                    "org-id": org_id,
                },
            ),
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(
                    labels={
                        "app": "dagster-code",
                        "org-id": org_id,
                        "managed-by": "kolkhis",
                    },
                ),
                spec=client.V1PodSpec(
                    containers=[
                        client.V1Container(
                            name="dagster-code",
                            image=DAGSTER_IMAGE,
                            ports=[
                                client.V1ContainerPort(
                                    container_port=3030, name="grpc"
                                ),
                                client.V1ContainerPort(
                                    container_port=3031, name="reload"
                                ),
                            ],
                            env_from=[
                                client.V1EnvFromSource(
                                    secret_ref=client.V1SecretEnvSource(
                                        name=names["secret"],
                                    ),
                                ),
                            ],
                        ),
                    ],
                ),
            ),
        ),
    )
    _apps_v1.create_namespaced_deployment(
        namespace=DAGSTER_NAMESPACE, body=deployment
    )


def _create_service(org_id: str, names: dict) -> None:
    """Create a ClusterIP Service for the org's dagster-code pod."""
    service = client.V1Service(
        api_version="v1",
        kind="Service",
        metadata=client.V1ObjectMeta(
            name=names["service"],
            namespace=DAGSTER_NAMESPACE,
            labels={
                "app": "dagster-code",
                "org-id": org_id,
                "managed-by": "kolkhis",
            },
        ),
        spec=client.V1ServiceSpec(
            selector={
                "app": "dagster-code",
                "org-id": org_id,
            },
            ports=[
                client.V1ServicePort(port=3030, target_port=3030, name="grpc"),
                client.V1ServicePort(port=3031, target_port=3031, name="reload"),
            ],
        ),
    )
    _core_v1.create_namespaced_service(namespace=DAGSTER_NAMESPACE, body=service)


def _update_workspace_configmap(org_id: str, names: dict) -> None:
    """Add a grpc_server entry to the shared dagster-workspace ConfigMap."""
    try:
        cm = _core_v1.read_namespaced_config_map(
            name=WORKSPACE_CONFIGMAP, namespace=DAGSTER_NAMESPACE
        )
    except client.ApiException as e:
        if e.status == 404:
            # Create the ConfigMap if it doesn't exist
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
            _core_v1.create_namespaced_config_map(
                namespace=DAGSTER_NAMESPACE, body=cm
            )
        else:
            raise

    # Parse existing workspace.yaml
    workspace = yaml.safe_load(cm.data.get("workspace.yaml", "load_from: []"))
    if not workspace or "load_from" not in workspace:
        workspace = {"load_from": []}

    service_host = f"{names['service']}.{DAGSTER_NAMESPACE}.svc.cluster.local"
    location_name = f"org-{org_id}"

    # Check if entry already exists
    for entry in workspace["load_from"]:
        if isinstance(entry, dict) and "grpc_server" in entry:
            if entry["grpc_server"].get("location_name") == location_name:
                log.info("Workspace entry for %s already exists", location_name)
                return

    # Append new entry
    workspace["load_from"].append({
        "grpc_server": {
            "host": service_host,
            "port": 3030,
            "location_name": location_name,
        }
    })

    cm.data["workspace.yaml"] = yaml.dump(workspace, default_flow_style=False)
    _core_v1.replace_namespaced_config_map(
        name=WORKSPACE_CONFIGMAP, namespace=DAGSTER_NAMESPACE, body=cm
    )
    log.info("Updated workspace ConfigMap with %s", location_name)


def _provision_sync(org_id: str) -> None:
    """Synchronous K8s provisioning for a dagster-code pod."""
    _init_k8s()
    names = _resource_names(org_id)

    steps = [
        ("secret", _create_secret),
        ("deployment", _create_deployment),
        ("service", _create_service),
        ("configmap", lambda oid, n: _update_workspace_configmap(oid, n)),
    ]

    for rtype, create_fn in steps:
        try:
            create_fn(org_id, names)
            log.info("Created dagster %s for org %s", rtype, org_id)
        except client.ApiException as e:
            if e.status == 409:
                log.info("Dagster %s already exists for org %s", rtype, org_id)
            else:
                log.error(
                    "Failed to create dagster %s for org %s: %s",
                    rtype, org_id, e.reason,
                )
                raise


async def provision_dagster_pod(org_id: str) -> str:
    """Provision K8s resources for an org's dagster-code pod. Returns the service name."""
    names = _resource_names(org_id)
    await asyncio.to_thread(_provision_sync, org_id)
    service_name = names["service"]
    log.info("Dagster pod provisioned for org %s at %s", org_id, service_name)
    return service_name


def dagster_reload_url_for_org(org_id: str) -> str:
    """Return the reload API URL for an org's dagster-code pod (K8s mode)."""
    names = _resource_names(org_id)
    return f"http://{names['service']}.{DAGSTER_NAMESPACE}.svc.cluster.local:3031"
```

**Step 2: Commit**

```bash
git add backend/app/dagster_k8s.py
git commit -m "Add dagster_k8s.py for per-org code location provisioning"
```

---

### Task 4: Update `create_org()` to provision dagster pod in K8s mode

**Files:**
- Modify: `backend/app/routers/orgs.py:11-15` (imports), lines 204-221 (dagster reload block), lines 228-230 (k8s provisioning block)

**Step 1: Add imports**

Update imports to include `DAGSTER_MODE`:

```python
from app.config import (
    S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY, S3_REGION, S3_BUCKET_NAME,
    SHELL_MODE, DAGSTER_CODE_URL, DAGSTER_RELOAD_TOKEN, SHELL_BACKEND_URL,
    DAGSTER_MODE,
)
```

**Step 2: Add background task for dagster K8s provisioning**

After `_provision_shell_k8s`, add:

```python
async def _provision_dagster_k8s(org_id: str) -> None:
    """Background task: provision K8s dagster-code pod for an org."""
    try:
        from app.dagster_k8s import provision_dagster_pod
        await provision_dagster_pod(org_id)
        logger.info("K8s dagster pod provisioned for org %s", org_id)
    except Exception:
        logger.exception("K8s dagster provisioning failed for org %s", org_id)
```

**Step 3: Branch dagster reload by mode**

Replace the current dagster reload block (lines 204-221) with:

```python
        # Prime dagster-code with the new org's repo
        if DAGSTER_MODE == "k8s":
            # In K8s mode, provision per-org dagster-code pod (background)
            asyncio.create_task(_provision_dagster_k8s(org.id))
        else:
            # Local mode: reload the single dagster-code container
            try:
                headers = {}
                if DAGSTER_RELOAD_TOKEN:
                    headers["Authorization"] = f"Bearer {DAGSTER_RELOAD_TOKEN}"
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        f"{DAGSTER_CODE_URL}/reload",
                        json={
                            "org_id": org.id,
                            "repo": WAREHOUSE_REPO,
                            "auth_token": make_service_token(org.id),
                            "backend_url": SHELL_BACKEND_URL,
                        },
                        headers=headers,
                    )
            except Exception:
                logger.warning("dagster-code reload failed for org %s (non-fatal)", org.id)
```

**Step 4: Commit**

```bash
git add backend/app/routers/orgs.py
git commit -m "Provision per-org dagster pod in K8s mode on org creation"
```

---

### Task 5: Update CI scaffold to use org-specific dagster service name

**Files:**
- Modify: `backend/app/routers/orgs.py:111-133` (CI scaffold in `_WAREHOUSE_SCAFFOLD`)

In K8s mode, the CI workflow needs to curl `dagster-code-{org_id}:3031/reload` instead of the static `dagster-code:3031`. Since the scaffold is generated per-org, we can embed the org ID.

**Step 1: Update the CI scaffold template**

The scaffold currently has a hardcoded `dagster-code:3031`. Change it to use a `{dagster_service}` placeholder:

```python
    ".gitea/workflows/ci.yml": """\
name: CI
on: [push]
jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: |
          apt-get update -qq && apt-get install -y -qq python3 python3-pip > /dev/null 2>&1
          pip install -q --break-system-packages sqlfluff
      - run: sqlfluff lint models/ --dialect duckdb
  deploy:
    runs-on: ubuntu-latest
    needs: lint
    steps:
      - run: |
          REPO_OWNER=$(echo $GITHUB_REPOSITORY | cut -d/ -f1)
          curl -sf -X POST http://{dagster_service}:3031/reload \\
            -H 'Content-Type: application/json' \\
            -H 'Authorization: Bearer dagster-reload-dev' \\
            -d "{\\"org_id\\": \\"$REPO_OWNER\\", \\"repo\\": \\"warehouse\\"}"
""",
```

**Step 2: Update scaffold rendering to resolve `{dagster_service}`**

In `create_org()`, where the scaffold is rendered (around line 194), update to also replace `{dagster_service}`:

```python
        dagster_service = f"dagster-code-{org.id}" if DAGSTER_MODE == "k8s" else "dagster-code"
        scaffold = {
            path: content.replace("{name}", body.name).replace("{dagster_service}", dagster_service)
            for path, content in _WAREHOUSE_SCAFFOLD.items()
        }
```

**Step 3: Commit**

```bash
git add backend/app/routers/orgs.py
git commit -m "Use org-specific dagster service name in CI scaffold for K8s mode"
```

---

### Task 6: Add dagster reload routing for K8s mode

**Files:**
- Modify: `backend/app/routers/orgs.py` (or wherever dagster reload is triggered outside of `create_org`)

In K8s mode, when the CI workflow or backend needs to reload an org's dagster-code, it should target the org-specific service. This is already handled by:
- CI scaffold (Task 5) — uses `dagster-code-{org_id}:3031`
- `create_org()` (Task 4) — provisions the pod instead of reloading

No additional changes needed for the current scope. The `dagster_k8s.dagster_reload_url_for_org()` helper is available for future use if the backend needs to trigger reloads for existing orgs.

**Step 1: Verify no other reload call sites exist**

Search for `DAGSTER_CODE_URL` usage in the codebase. It should only appear in `config.py` (definition) and `orgs.py` (reload call). If there are other call sites, they need the same local/k8s branching.

**Step 2: No code changes — skip this task if no other call sites found**

---

### Task 7: End-to-end verification

**No code changes — manual verification.**

**For local mode:**

1. Ensure `DAGSTER_MODE` is unset or `local` in `.env`
2. `docker compose up -d dagster-code dagster-webserver dagster-daemon`
3. Create an org via the UI — verify dagster reload works as before
4. Push to warehouse repo — verify CI scaffold curls `dagster-code:3031`

**For K8s mode (production):**

1. Set `DAGSTER_MODE=k8s`, `DAGSTER_IMAGE=...`, `DAGSTER_NAMESPACE=kolkhis` in backend env
2. Ensure backend ServiceAccount has RBAC for: Secrets, Deployments, Services, ConfigMaps in the namespace
3. Create an org — verify:
   - Secret `dagster-code-{org_id}` created
   - Deployment `dagster-code-{org_id}` running
   - Service `dagster-code-{org_id}` created
   - ConfigMap `dagster-workspace` updated with new grpc_server entry
4. Pod should auto-clone the org's warehouse repo and start gRPC
5. Dagster webserver should discover the new code location
