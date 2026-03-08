import asyncio
import logging

import s3fs
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.config import S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY, S3_REGION, S3_BUCKET_NAME, SHELL_MODE
from app.database import get_db, async_session
from app.gitea import create_gitea_org, create_repo, create_files_batch
from app.models import OrgDatabase, Organization, OrgMembership, User
from app.shell import ensure_shell_user
from app.warehouse import ducklake_data_path, ducklake_metadata_schema
from app.workspace import ensure_clone

logger = logging.getLogger(__name__)

WAREHOUSE_REPO = "warehouse"

router = APIRouter(prefix="/api/orgs")


class CreateOrgRequest(BaseModel):
    name: str


class JoinOrgRequest(BaseModel):
    org_id: str


class ApproveMemberRequest(BaseModel):
    user_id: int


async def _create_org_storage(org_id: str, db: AsyncSession) -> None:
    """Create S3 prefix and initial 'development' database for the org."""
    fs = s3fs.S3FileSystem(
        endpoint_url=S3_ENDPOINT,
        key=S3_ACCESS_KEY,
        secret=S3_SECRET_KEY,
        client_kwargs={"region_name": S3_REGION},
    )
    await asyncio.to_thread(fs.mkdirs, f"{S3_BUCKET_NAME}/{org_id}", exist_ok=True)

    # Create the 'development' database record (DuckLake auto-creates metadata schema on first ATTACH)
    data_path = ducklake_data_path(org_id, "development")
    metadata_schema = ducklake_metadata_schema(org_id, "development")
    org_db = OrgDatabase(
        org_id=org_id, name="development",
        data_path=data_path, metadata_schema=metadata_schema,
    )
    db.add(org_db)


# dbt + dagster scaffold for the warehouse monorepo
_WAREHOUSE_SCAFFOLD = {
    "dbt_project.yml": """\
name: '{name}'
version: '1.0.0'
config-version: 2

profile: '{name}'

model-paths: ["models"]
macro-paths: ["macros"]
seed-paths: ["seeds"]
test-paths: ["tests"]

dispatch:
  - macro_namespace: dbt
    search_order: ['kolkhis', 'dbt']
""",
    "profiles.yml": """\
'{name}':
  target: dev
  outputs:
    dev:
      type: kolkhis
      backend_url: "{{ env_var('KOLKHIS_BACKEND_URL') }}"
      auth_token: "{{ env_var('KOLKHIS_AUTH_TOKEN') }}"
      database: development
      schema: "dbt_{{ env_var('DBT_USER') }}"
""",
    ".gitignore": """\
# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/

# dbt
target/
logs/
dbt_packages/

# Dagster
.dagster/
""",
    "models/.gitkeep": "",
    "macros/.gitkeep": "",
    "seeds/.gitkeep": "",
    "tests/.gitkeep": "",
    "dagster/.gitkeep": "",
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
""",
}


async def _provision_shell_k8s(org_id: str) -> None:
    """Background task: provision K8s shell pod for an org."""
    try:
        from app.shell_k8s import provision_shell_pod
        async with async_session() as db:
            await provision_shell_pod(org_id, db)
        logger.info("K8s shell pod provisioned for org %s", org_id)
    except Exception:
        logger.exception("K8s shell provisioning failed for org %s", org_id)


async def _provision_workspace(
    user_id: int, org_id: str, email: str, user_name: str,
) -> None:
    """Background task: provision shell user and clone warehouse repo."""
    try:
        async with async_session() as db:
            shell_username, gitea_org = await ensure_shell_user(user_id, org_id, email, db)
            await ensure_clone(
                org_id, shell_username, WAREHOUSE_REPO,
                user_name, email, owner=gitea_org,
            )
        logger.info("Workspace provisioned for user %d in org %s", user_id, org_id)
    except Exception:
        logger.exception("Background workspace provisioning failed for user %d in org %s", user_id, org_id)


@router.post("")
async def create_org(
    body: CreateOrgRequest,
    auth: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Create a new organization. The creating user becomes admin."""
    # Check name uniqueness
    result = await db.execute(
        select(Organization).where(Organization.name == body.name)
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Organization name already taken")

    org = Organization(name=body.name)
    db.add(org)
    await db.flush()

    membership = OrgMembership(
        user_id=int(auth["sub"]),
        org_id=org.id,
        role="admin",
        status="active",
    )
    db.add(membership)

    # Provision Gitea org + warehouse repo + S3 bucket
    try:
        await create_gitea_org(org.id)
        await create_repo(WAREHOUSE_REPO, owner=org.id)
        scaffold = {
            path: content.replace("{name}", body.name)
            for path, content in _WAREHOUSE_SCAFFOLD.items()
        }
        await create_files_batch(
            WAREHOUSE_REPO, scaffold,
            message="Initialize warehouse scaffold",
            owner=org.id,
        )
        await _create_org_storage(org.id, db)
    except Exception as exc:
        logger.error("Org provisioning failed for %s: %s", org.id, exc)
        raise HTTPException(status_code=502, detail="Failed to provision organization")

    await db.commit()

    # In K8s mode, provision the org's shell pod before workspace setup
    if SHELL_MODE == "k8s":
        asyncio.create_task(_provision_shell_k8s(org.id))

    # Provision shell user + clone repo in background
    asyncio.create_task(_provision_workspace(
        user_id=int(auth["sub"]), org_id=org.id, email=auth.get("email", ""),
        user_name=auth.get("name", ""),
    ))

    return {"id": org.id, "name": org.name}


@router.get("")
async def list_orgs(
    auth: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List all organizations (for the join screen)."""
    result = await db.execute(select(Organization).order_by(Organization.name))
    orgs = result.scalars().all()
    return [{"id": o.id, "name": o.name} for o in orgs]


@router.get("/mine")
async def list_my_orgs(
    auth: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List organizations the current user belongs to."""
    user_id = int(auth["sub"])
    result = await db.execute(
        select(OrgMembership, Organization)
        .join(Organization, OrgMembership.org_id == Organization.id)
        .where(OrgMembership.user_id == user_id)
        .order_by(Organization.name)
    )
    rows = result.all()
    return [
        {
            "id": org.id,
            "name": org.name,
            "role": mem.role,
            "status": mem.status,
        }
        for mem, org in rows
    ]


@router.post("/{org_id}/join")
async def join_org(
    org_id: str,
    auth: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Request to join an organization (pending approval)."""
    # Verify org exists
    result = await db.execute(
        select(Organization).where(Organization.id == org_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Organization not found")

    user_id = int(auth["sub"])

    # Check if already a member
    result = await db.execute(
        select(OrgMembership).where(
            OrgMembership.user_id == user_id,
            OrgMembership.org_id == org_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Already a member or pending")

    membership = OrgMembership(
        user_id=user_id,
        org_id=org_id,
        role="member",
        status="pending",
    )
    db.add(membership)
    await db.commit()

    return {"detail": "Join request submitted"}


@router.get("/{org_id}/members")
async def list_members(
    org_id: str,
    auth: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List org members. Only active members can view."""
    user_id = int(auth["sub"])

    # Verify caller is an active member
    result = await db.execute(
        select(OrgMembership).where(
            OrgMembership.user_id == user_id,
            OrgMembership.org_id == org_id,
            OrgMembership.status == "active",
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Not a member of this organization")

    result = await db.execute(
        select(OrgMembership, User)
        .join(User, OrgMembership.user_id == User.id)
        .where(OrgMembership.org_id == org_id)
        .order_by(OrgMembership.status, User.name)
    )
    rows = result.all()
    return [
        {
            "user_id": user.id,
            "name": user.name,
            "email": user.email,
            "role": mem.role,
            "status": mem.status,
        }
        for mem, user in rows
    ]


@router.post("/{org_id}/members/{user_id}/approve")
async def approve_member(
    org_id: str,
    user_id: int,
    auth: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Approve a pending member. Only admins can approve."""
    caller_id = int(auth["sub"])

    # Verify caller is admin
    result = await db.execute(
        select(OrgMembership).where(
            OrgMembership.user_id == caller_id,
            OrgMembership.org_id == org_id,
            OrgMembership.role == "admin",
            OrgMembership.status == "active",
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="Admin access required")

    # Find pending membership
    result = await db.execute(
        select(OrgMembership).where(
            OrgMembership.user_id == user_id,
            OrgMembership.org_id == org_id,
            OrgMembership.status == "pending",
        )
    )
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=404, detail="No pending membership found")

    membership.status = "active"

    # Get user details for workspace provisioning
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one()

    await db.commit()

    # Provision shell user + clone repo in background
    asyncio.create_task(_provision_workspace(
        user_id=user_id, org_id=org_id, email=user.email,
        user_name=user.name,
    ))

    return {"detail": "Member approved"}
