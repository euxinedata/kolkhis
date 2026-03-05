from fastapi import APIRouter, Depends, HTTPException

from app.auth import require_auth
from app.warehouse import get_org_catalog

router = APIRouter(prefix="/api/catalog")


def _get_catalog(auth: dict):
    """Get the PyIceberg catalog for the authenticated user's org."""
    org_id = auth.get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="No active organization")
    return get_org_catalog(org_id)


@router.get("/databases")
async def list_databases(auth: dict = Depends(require_auth)):
    catalog = _get_catalog(auth)
    # Top-level namespaces are databases
    top = catalog.list_namespaces()
    return [{"name": ns[0]} for ns in top]


@router.get("/databases/{db_name}/schemas")
async def list_schemas(db_name: str, auth: dict = Depends(require_auth)):
    catalog = _get_catalog(auth)
    # Child namespaces under the database are schemas
    children = catalog.list_namespaces((db_name,))
    return [{"name": ns[-1]} for ns in children]


@router.get("/databases/{db_name}/schemas/{schema_name}/objects")
async def list_objects(
    db_name: str, schema_name: str, auth: dict = Depends(require_auth),
):
    catalog = _get_catalog(auth)
    ns = f"{db_name}.{schema_name}"
    tables = catalog.list_tables(ns)
    return [{"name": t[-1], "type": "table"} for t in tables]


@router.get("/databases/{db_name}/schemas/{schema_name}/objects/{obj_name}/schema")
async def get_object_schema(
    db_name: str, schema_name: str, obj_name: str,
    auth: dict = Depends(require_auth),
):
    catalog = _get_catalog(auth)
    iceberg_id = f"{db_name}.{schema_name}.{obj_name}"
    try:
        tbl = catalog.load_table(iceberg_id)
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
