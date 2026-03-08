from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.database import get_db
from app.models import OrgDatabase

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


async def _schema_exists(db: AsyncSession, meta: str) -> bool:
    """Check if the DuckLake metadata schema exists in PostgreSQL."""
    result = await db.execute(
        text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :name"),
        {"name": meta},
    )
    return result.scalar() is not None


@router.get("/databases/{db_name}/schemas")
async def list_schemas(
    db_name: str,
    auth: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    org_db = await _get_org_db(db_name, auth, db)
    meta = org_db.metadata_schema

    if not await _schema_exists(db, meta):
        return {"schemas": [], "total_size": 0, "total_tables": 0, "last_updated_ms": None}

    result = await db.execute(text(
        f"SELECT s.schema_name, "
        f"  COUNT(DISTINCT t.table_id) AS table_count, "
        f"  COALESCE(SUM(ts.file_size_bytes), 0) AS file_size "
        f'FROM "{meta}".ducklake_schema s '
        f'LEFT JOIN "{meta}".ducklake_table t '
        f"  ON s.schema_id = t.schema_id AND t.end_snapshot IS NULL "
        f'LEFT JOIN "{meta}".ducklake_table_stats ts ON t.table_id = ts.table_id '
        f"WHERE s.end_snapshot IS NULL "
        f"GROUP BY s.schema_name ORDER BY s.schema_name"
    ))
    rows = result.all()

    schemas = []
    total_tables = 0
    total_size = 0
    for schema_name, table_count, file_size in rows:
        total_tables += table_count
        total_size += file_size
        schemas.append({"name": schema_name, "tables": table_count, "file_size": file_size})

    # Last updated from most recent snapshot
    snap_result = await db.execute(text(
        f"SELECT MAX(snapshot_time) "
        f'FROM "{meta}".ducklake_snapshot'
    ))
    last_snap = snap_result.scalar()
    last_updated_ms = int(last_snap.timestamp() * 1000) if last_snap else None

    return {
        "schemas": schemas,
        "total_size": total_size,
        "total_tables": total_tables,
        "last_updated_ms": last_updated_ms,
    }


@router.get("/databases/{db_name}/schemas/{schema_name}/objects")
async def list_objects(
    db_name: str,
    schema_name: str,
    auth: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    org_db = await _get_org_db(db_name, auth, db)
    meta = org_db.metadata_schema

    if not await _schema_exists(db, meta):
        return {"objects": [], "total_size": 0, "last_updated_ms": None}

    # Get schema_id
    schema_id_result = await db.execute(text(
        f'SELECT schema_id FROM "{meta}".ducklake_schema WHERE schema_name = :name AND end_snapshot IS NULL'
    ), {"name": schema_name})
    schema_row = schema_id_result.first()
    if schema_row is None:
        return {"objects": [], "total_size": 0, "last_updated_ms": None}
    schema_id = schema_row[0]

    result_list = []

    # Tables with column count and file size
    table_result = await db.execute(text(
        f"SELECT t.table_id, t.table_name, "
        f"  (SELECT COUNT(*) FROM \"{meta}\".ducklake_column c "
        f"   WHERE c.table_id = t.table_id AND c.end_snapshot IS NULL) AS col_count, "
        f"  ts.file_size_bytes "
        f'FROM "{meta}".ducklake_table t '
        f'LEFT JOIN "{meta}".ducklake_table_stats ts ON t.table_id = ts.table_id '
        f"WHERE t.schema_id = :schema_id AND t.end_snapshot IS NULL "
        f"ORDER BY t.table_name"
    ), {"schema_id": schema_id})
    total_size = 0
    for _table_id, table_name, col_count, file_size in table_result.all():
        total_size += file_size or 0
        result_list.append({
            "name": table_name, "type": "table",
            "columns": col_count, "file_size": file_size,
        })

    # Views
    try:
        view_result = await db.execute(text(
            f'SELECT v.view_name FROM "{meta}".ducklake_view v '
            f"WHERE v.schema_id = :schema_id AND v.end_snapshot IS NULL "
            f"ORDER BY v.view_name"
        ), {"schema_id": schema_id})
        for (view_name,) in view_result.all():
            result_list.append({"name": view_name, "type": "view", "columns": None, "file_size": None})
    except Exception:
        pass

    # Last updated from most recent snapshot
    snap_result = await db.execute(text(
        f"SELECT MAX(s.snapshot_time) "
        f'FROM "{meta}".ducklake_snapshot s'
    ))
    last_snap = snap_result.scalar()
    last_updated_ms = int(last_snap.timestamp() * 1000) if last_snap else None

    return {"objects": result_list, "total_size": total_size, "last_updated_ms": last_updated_ms}


@router.get("/databases/{db_name}/schemas/{schema_name}/objects/{obj_name}/schema")
async def get_object_schema(
    db_name: str,
    schema_name: str,
    obj_name: str,
    auth: dict = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    org_db = await _get_org_db(db_name, auth, db)
    meta = org_db.metadata_schema

    if not await _schema_exists(db, meta):
        raise HTTPException(status_code=404, detail=f"Object '{db_name}.{schema_name}.{obj_name}' not found")

    # Get schema_id
    schema_id_result = await db.execute(text(
        f'SELECT schema_id FROM "{meta}".ducklake_schema WHERE schema_name = :name AND end_snapshot IS NULL'
    ), {"name": schema_name})
    schema_row = schema_id_result.first()
    if schema_row is None:
        raise HTTPException(status_code=404, detail=f"Object '{db_name}.{schema_name}.{obj_name}' not found")
    schema_id = schema_row[0]

    # Try table first
    table_result = await db.execute(text(
        f'SELECT t.table_id FROM "{meta}".ducklake_table t '
        f"WHERE t.schema_id = :schema_id AND t.table_name = :name AND t.end_snapshot IS NULL"
    ), {"schema_id": schema_id, "name": obj_name})
    table_row = table_result.first()

    if table_row is not None:
        table_id = table_row[0]
        # Get columns
        col_result = await db.execute(text(
            f'SELECT c.column_name, c.column_type FROM "{meta}".ducklake_column c '
            f"WHERE c.table_id = :table_id AND c.end_snapshot IS NULL "
            f"ORDER BY c.column_order"
        ), {"table_id": table_id})
        columns = [
            {"name": row[0], "type": row[1], "required": False}
            for row in col_result.all()
        ]

        # Table stats
        stats_result = await db.execute(text(
            f"SELECT ts.record_count, ts.file_size_bytes "
            f'FROM "{meta}".ducklake_table_stats ts '
            f"WHERE ts.table_id = :table_id"
        ), {"table_id": table_id})
        stats_row = stats_result.first()
        row_count = stats_row[0] if stats_row else None
        file_size = stats_row[1] if stats_row else None

        # File count and last updated
        file_result = await db.execute(text(
            f"SELECT COUNT(df.data_file_id), MAX(s.snapshot_time) "
            f'FROM "{meta}".ducklake_data_file df '
            f'LEFT JOIN "{meta}".ducklake_snapshot s ON df.begin_snapshot = s.snapshot_id '
            f"WHERE df.table_id = :table_id AND df.end_snapshot IS NULL"
        ), {"table_id": table_id})
        file_row = file_result.first()
        file_count = file_row[0] if file_row else None
        last_snap = file_row[1] if file_row else None
        last_updated_ms = int(last_snap.timestamp() * 1000) if last_snap else None

        # Snapshot history — find snapshots that added files to this table
        snap_result = await db.execute(text(
            f"SELECT DISTINCT s.snapshot_id, s.snapshot_time "
            f'FROM "{meta}".ducklake_snapshot s '
            f'JOIN "{meta}".ducklake_data_file df ON df.begin_snapshot = s.snapshot_id '
            f"WHERE df.table_id = :table_id "
            f"ORDER BY s.snapshot_id DESC LIMIT 20"
        ), {"table_id": table_id})
        snapshots = [
            {"snapshot_id": sid, "timestamp_ms": int(stime.timestamp() * 1000)}
            for sid, stime in snap_result.all()
        ]

        return {
            "type": "table",
            "row_count": row_count,
            "total_file_size": file_size,
            "total_data_files": file_count,
            "last_updated_ms": last_updated_ms,
            "partition_fields": None,
            "snapshots": snapshots,
            "columns": columns,
        }

    # Try view
    try:
        view_result = await db.execute(text(
            f'SELECT v.sql FROM "{meta}".ducklake_view v '
            f"WHERE v.schema_id = :schema_id AND v.view_name = :name AND v.end_snapshot IS NULL"
        ), {"schema_id": schema_id, "name": obj_name})
        view_row = view_result.first()
        if view_row is not None:
            return {
                "type": "view",
                "view_sql": view_row[0],
                "row_count": None,
                "total_file_size": None,
                "total_data_files": None,
                "last_updated_ms": None,
                "partition_fields": None,
                "snapshots": [],
                "columns": [],
            }
    except Exception:
        pass

    raise HTTPException(status_code=404, detail=f"Object '{db_name}.{schema_name}.{obj_name}' not found")
