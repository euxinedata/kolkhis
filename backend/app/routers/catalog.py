from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.database import get_db
from app.models import OrgDatabase
from app.warehouse import get_database_catalog

router = APIRouter(prefix="/api/catalog")


async def _get_org_db(db_name: str, auth: dict, db: AsyncSession) -> OrgDatabase:
    """Look up an OrgDatabase by name for the authenticated user's org."""
    org_id = auth.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No active organization")
    result = await db.execute(
        select(OrgDatabase).where(
            OrgDatabase.org_id == org_id,
            OrgDatabase.name == db_name,
        )
    )
    org_db = result.scalar_one_or_none()
    if org_db is None:
        raise HTTPException(status_code=404, detail=f"Database '{db_name}' not found")
    return org_db


@router.get("/databases")
async def list_databases(
    auth: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    org_id = auth.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No active organization")
    result = await db.execute(
        select(OrgDatabase).where(OrgDatabase.org_id == org_id).order_by(OrgDatabase.name)
    )
    databases = result.scalars().all()
    return [{"name": d.name} for d in databases]


@router.get("/databases/{db_name}/schemas")
async def list_schemas(
    db_name: str,
    auth: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    org_db = await _get_org_db(db_name, auth, db)
    catalog = get_database_catalog(org_db.lakekeeper_warehouse)
    namespaces = catalog.list_namespaces()
    return [{"name": ns[0]} for ns in namespaces]


@router.get("/databases/{db_name}/schemas/{schema_name}/objects")
async def list_objects(
    db_name: str,
    schema_name: str,
    auth: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    org_db = await _get_org_db(db_name, auth, db)
    catalog = get_database_catalog(org_db.lakekeeper_warehouse)
    tables = catalog.list_tables(schema_name)
    return [{"name": t[-1], "type": "table"} for t in tables]


@router.get("/databases/{db_name}/schemas/{schema_name}/objects/{obj_name}/schema")
async def get_object_schema(
    db_name: str,
    schema_name: str,
    obj_name: str,
    auth: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    org_db = await _get_org_db(db_name, auth, db)
    catalog = get_database_catalog(org_db.lakekeeper_warehouse)
    try:
        tbl = catalog.load_table(f"{schema_name}.{obj_name}")
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {
        "type": "table",
        "columns": [
            {
                "name": field.name,
                "type": str(field.field_type),
                "required": field.required,
            }
            for field in tbl.schema().fields
        ],
    }
