# Multi-Org Dagster Code Locations Design

## Goal

Support multiple organizations with isolated Dagster code locations, using the same local/k8s dual-mode pattern as shell pods.

## Architecture

Two modes:

- **`DAGSTER_MODE=local`** (docker-compose) — Current setup unchanged. Single dagster-code container serves one org. Backend calls `/reload` with token. Webserver/daemon/code all in docker-compose.

- **`DAGSTER_MODE=k8s`** (production) — Per-org dagster-code pods provisioned by the backend via K8s API. Shared webserver + daemon discover code locations via a ConfigMap-based `workspace.yaml`. Each pod self-heals on restart.

## K8s Provisioning (per-org)

On org creation, the backend provisions:

1. **Secret** `dagster-code-{org_id}` — contains `KOLKHIS_AUTH_TOKEN`, `KOLKHIS_BACKEND_URL`, `DAGSTER_ORG_ID`, `DAGSTER_REPO` (defaults to `warehouse`)
2. **Deployment** `dagster-code-{org_id}` — single replica, same dagster-code image, mounts the Secret as env vars. Labels: `app: dagster-code`, `org-id: {org_id}`, `managed-by: kolkhis`
3. **Service** `dagster-code-{org_id}` — ClusterIP pointing to port 3030
4. **ConfigMap update** — Appends a new `grpc_server` entry to the shared `dagster-workspace` ConfigMap

The webserver and daemon mount the ConfigMap as `workspace.yaml`. When the ConfigMap changes, they pick up the new code location via gRPC heartbeat.

## Entrypoint Changes

On startup, if `DAGSTER_ORG_ID` is set (K8s mode), auto-clone and start gRPC:

```
startup:
  if DAGSTER_ORG_ID is set:
    clone repo from Gitea
    start gRPC if definitions.py exists
  start reload API on :3031
```

The `/reload` endpoint works the same in both modes — pull latest code, restart gRPC. In K8s mode, the token comes from env vars (Secret) rather than the reload payload.

## Backend Changes

New module `app/dagster_k8s.py` (mirroring `app/shell_k8s.py`):

- `provision_dagster_pod(org_id, db)` — creates Secret, Deployment, Service, updates ConfigMap
- Called from `create_org()` when `DAGSTER_MODE=k8s`

New config vars:
- `DAGSTER_MODE` — `local` or `k8s` (default: `local`)
- `DAGSTER_IMAGE` — container image for dagster-code pods
- `DAGSTER_NAMESPACE` — K8s namespace (default: same as `SHELL_NAMESPACE`)

## CI Integration

The CI workflow scaffold stays the same — it curls `dagster-code-{org_id}:3031/reload`. In K8s, the service name resolves via cluster DNS. In local mode, it uses the static `dagster-code:3031` from docker-compose.

## Migration Path

Local dev only ever has one org active, so no changes needed there. The K8s provisioning is additive — existing shell pod K8s infrastructure (ServiceAccount, RBAC) is extended to cover dagster-code resources.

Future scale-out to fully isolated Dagster instances per org (separate webserver/daemon) would only require changing the provisioning code, not the entrypoint or CI integration.
