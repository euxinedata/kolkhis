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
    result = []
    total_size = 0
    for t in tables:
        tbl = catalog.load_table(f"{schema_name}.{t[-1]}")
        col_count = len(tbl.schema().fields)
        file_size = None
        snapshot = tbl.current_snapshot()
        if snapshot and snapshot.summary:
            size = snapshot.summary.get("total-files-size")
            if size is not None:
                file_size = int(size)
                total_size += file_size
        result.append({"name": t[-1], "type": "table", "columns": col_count, "file_size": file_size})
    return {"objects": result, "total_size": total_size}


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

    row_count = None
    total_file_size = None
    total_data_files = None
    last_updated_ms = None
    snapshot = tbl.current_snapshot()
    if snapshot:
        last_updated_ms = snapshot.timestamp_ms
        if snapshot.summary:
            total = snapshot.summary.get("total-records")
            if total is not None:
                row_count = int(total)
            size = snapshot.summary.get("total-files-size")
            if size is not None:
                total_file_size = int(size)
            files = snapshot.summary.get("total-data-files")
            if files is not None:
                total_data_files = int(files)

    # Partition info
    spec = tbl.spec()
    partition_fields = None
    if not spec.is_unpartitioned():
        schema = tbl.schema()
        partition_fields = []
        for pf in spec.fields:
            source_field = schema.find_field(pf.source_id)
            partition_fields.append({
                "name": source_field.name,
                "transform": str(pf.transform),
            })

    # Recent snapshots (last 10)
    snapshots = []
    for snap in reversed(tbl.snapshots()[-10:]):
        entry = {
            "snapshot_id": snap.snapshot_id,
            "timestamp_ms": snap.timestamp_ms,
        }
        if snap.summary:
            entry["operation"] = str(snap.summary.operation.value)
            added = snap.summary.get("added-records")
            if added is not None:
                entry["added_records"] = int(added)
            deleted = snap.summary.get("deleted-records")
            if deleted is not None:
                entry["deleted_records"] = int(deleted)
        snapshots.append(entry)

    return {
        "type": "table",
        "row_count": row_count,
        "total_file_size": total_file_size,
        "total_data_files": total_data_files,
        "last_updated_ms": last_updated_ms,
        "partition_fields": partition_fields,
        "snapshots": snapshots,
        "columns": [
            {
                "name": field.name,
                "type": str(field.field_type),
                "required": field.required,
            }
            for field in tbl.schema().fields
        ],
    }
