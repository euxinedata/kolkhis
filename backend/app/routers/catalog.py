from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import require_auth
from app.warehouse import catalog

router = APIRouter(prefix="/api/catalog")


class CreateNamespace(BaseModel):
    name: str


class ColumnSchema(BaseModel):
    name: str
    type: str
    required: bool = True


class CreateTable(BaseModel):
    columns: list[ColumnSchema]


@router.get("/namespaces")
async def list_namespaces(_user: dict = Depends(require_auth)):
    namespaces = catalog.list_namespaces()
    return [ns[0] for ns in namespaces]


@router.post("/namespaces")
async def create_namespace(body: CreateNamespace, _user: dict = Depends(require_auth)):
    catalog.create_namespace(body.name)
    return {"name": body.name}


@router.get("/namespaces/{ns}/tables")
async def list_tables(ns: str, _user: dict = Depends(require_auth)):
    tables = catalog.list_tables(ns)
    return [t[1] for t in tables]


@router.get("/tables/{ns}/{table}/schema")
async def get_table_schema(ns: str, table: str, _user: dict = Depends(require_auth)):
    try:
        tbl = catalog.load_table(f"{ns}.{table}")
    except Exception as e:
        raise HTTPException(status_code=404, detail=str(e))
    schema = tbl.schema()
    return {
        "columns": [
            {
                "name": field.name,
                "type": str(field.field_type),
                "required": field.required,
            }
            for field in schema.fields
        ]
    }


@router.post("/tables/{ns}/{table}")
async def create_table(
    ns: str,
    table: str,
    body: CreateTable,
    _user: dict = Depends(require_auth),
):
    import pyarrow as pa

    type_map = {
        "string": pa.string(),
        "int": pa.int32(),
        "integer": pa.int32(),
        "int32": pa.int32(),
        "int64": pa.int64(),
        "long": pa.int64(),
        "float": pa.float32(),
        "float32": pa.float32(),
        "float64": pa.float64(),
        "double": pa.float64(),
        "boolean": pa.bool_(),
        "bool": pa.bool_(),
        "date": pa.date32(),
        "timestamp": pa.timestamp("us"),
    }

    fields = []
    for col in body.columns:
        pa_type = type_map.get(col.type.lower())
        if pa_type is None:
            raise HTTPException(
                status_code=400, detail=f"Unsupported type: {col.type}"
            )
        fields.append(pa.field(col.name, pa_type, nullable=not col.required))

    pa_schema = pa.schema(fields)
    catalog.create_table(f"{ns}.{table}", schema=pa_schema)
    return {"namespace": ns, "table": table}
