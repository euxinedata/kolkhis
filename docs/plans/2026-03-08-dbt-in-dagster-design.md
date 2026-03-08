# dbt-in-Dagster Scheduling Design

## Goal

Enable scheduled dbt model runs inside the dagster-code container, using the dagster-dbt integration and a service JWT for authentication.

## Architecture

The backend generates a long-lived service JWT per org (sub: "service:dagster", no expiry) and passes it to dagster-code via the /reload payload. dagster-code stores the token in memory and sets it as an env var when starting the gRPC subprocess. Dagster definitions use `DbtCliResource` from `dagster-dbt` for native asset integration.

## Data Flow

1. Backend calls `POST /reload` with `{"org_id": "...", "repo": "warehouse", "auth_token": "eyJ...", "backend_url": "http://..."}`
2. dagster-code clones/pulls the repo, stores token and URL
3. dagster-code starts gRPC subprocess with env vars: `KOLKHIS_AUTH_TOKEN`, `KOLKHIS_BACKEND_URL`, `DBT_USER=dagster`
4. Dagster definitions use `DbtCliResource(project_dir="..")` — dbt shells out to `dbt run`
5. dbt-kolkhis adapter reads env vars, connects to backend session proxy
6. Backend creates DuckLake session on worker, executes SQL

## Changes

### backend/app/auth.py
Add `make_service_token(org_id)` — mints JWT with `sub: "service:dagster"`, `org_id`, no expiry.

### backend/app/routers/orgs.py
Include `auth_token` and `backend_url` in the /reload payload.

### backend/app/routers/dbt.py
Handle non-numeric `sub` in QueryJob recording (skip or use None for user_id).

### docker/dagster/entrypoint.py
Store `auth_token` and `backend_url` from reload payload, pass as env vars to gRPC subprocess.

### docker/dagster/Dockerfile
Install `dbt-kolkhis` from git.

### docker-compose.yml
Pass `SHELL_BACKEND_URL` to dagster-code.

## Service Token

```python
def make_service_token(org_id: str) -> str:
    return jwt.encode(
        {"sub": "service:dagster", "org_id": org_id, "name": "dagster"},
        JWT_SECRET, algorithm="HS256",
    )  # No exp — long-lived service token
```

## dbt Profile

Already scaffolded in warehouse repo `profiles.yml`:
```yaml
'{name}':
  target: dev
  outputs:
    dev:
      type: kolkhis
      backend_url: "{{ env_var('KOLKHIS_BACKEND_URL') }}"
      auth_token: "{{ env_var('KOLKHIS_AUTH_TOKEN') }}"
      database: development
      schema: "dbt_{{ env_var('DBT_USER') }}"
```

`DBT_USER=dagster` → schema `dbt_dagster`.

## Example definitions.py

```python
from dagster import Definitions, AssetExecutionContext
from dagster_dbt import DbtCliResource, dbt_assets, DbtProject

project = DbtProject(project_dir="..")

@dbt_assets(manifest=project.manifest_path)
def warehouse_dbt_assets(context: AssetExecutionContext, dbt: DbtCliResource):
    yield from dbt.cli(["run"], context=context).stream()

defs = Definitions(
    assets=[warehouse_dbt_assets],
    resources={"dbt": DbtCliResource(project_dir="..")},
)
```
