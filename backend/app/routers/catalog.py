from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_auth
from app.database import get_db
from app.models import CatalogObject, Database, Schema
from app.warehouse import catalog

router = APIRouter(prefix="/api/catalog")


# --- Request/Response models ---


class CreateDatabase(BaseModel):
    name: str


class CreateSchema(BaseModel):
    name: str


class ColumnSchema(BaseModel):
    name: str
    type: str
    required: bool = True


class CreateTable(BaseModel):
    columns: list[ColumnSchema]


class CreateView(BaseModel):
    sql: str


# --- Helper to resolve database + schema ---


async def _get_database(db: AsyncSession, db_name: str) -> Database:
    result = await db.execute(select(Database).where(Database.name == db_name))
    database = result.scalar()
    if database is None:
        raise HTTPException(status_code=404, detail=f"Database '{db_name}' not found")
    return database


async def _get_schema(db: AsyncSession, db_name: str, schema_name: str) -> Schema:
    database = await _get_database(db, db_name)
    result = await db.execute(
        select(Schema).where(
            Schema.database_id == database.id, Schema.name == schema_name
        )
    )
    schema = result.scalar()
    if schema is None:
        raise HTTPException(
            status_code=404,
            detail=f"Schema '{schema_name}' not found in database '{db_name}'",
        )
    return schema


# --- Database endpoints ---


@router.get("/databases")
async def list_databases(
    db: AsyncSession = Depends(get_db), _user: dict = Depends(require_auth)
):
    result = await db.execute(select(Database).order_by(Database.name))
    return [{"name": d.name} for d in result.scalars().all()]


@router.post("/databases")
async def create_database(
    body: CreateDatabase,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_auth),
):
    # Check for duplicates
    result = await db.execute(select(Database).where(Database.name == body.name))
    if result.scalar() is not None:
        raise HTTPException(
            status_code=409, detail=f"Database '{body.name}' already exists"
        )
    database = Database(name=body.name)
    db.add(database)
    await db.commit()
    return {"name": database.name}


# --- Schema endpoints ---


@router.get("/databases/{db_name}/schemas")
async def list_schemas(
    db_name: str,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_auth),
):
    database = await _get_database(db, db_name)
    result = await db.execute(
        select(Schema).where(Schema.database_id == database.id).order_by(Schema.name)
    )
    return [{"name": s.name} for s in result.scalars().all()]


@router.post("/databases/{db_name}/schemas")
async def create_schema(
    db_name: str,
    body: CreateSchema,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_auth),
):
    database = await _get_database(db, db_name)
    result = await db.execute(
        select(Schema).where(
            Schema.database_id == database.id, Schema.name == body.name
        )
    )
    if result.scalar() is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Schema '{body.name}' already exists in database '{db_name}'",
        )
    schema = Schema(database_id=database.id, name=body.name)
    db.add(schema)
    await db.commit()
    return {"name": schema.name}


# --- Object endpoints ---


@router.get("/databases/{db_name}/schemas/{schema_name}/objects")
async def list_objects(
    db_name: str,
    schema_name: str,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_auth),
):
    schema = await _get_schema(db, db_name, schema_name)
    result = await db.execute(
        select(CatalogObject)
        .where(CatalogObject.schema_id == schema.id)
        .order_by(CatalogObject.name)
    )
    return [
        {"name": obj.name, "type": obj.object_type}
        for obj in result.scalars().all()
    ]


@router.post("/databases/{db_name}/schemas/{schema_name}/tables")
async def create_table(
    db_name: str,
    schema_name: str,
    table_name: str,
    body: CreateTable,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_auth),
):
    import pyarrow as pa

    schema = await _get_schema(db, db_name, schema_name)

    # Check for duplicates
    result = await db.execute(
        select(CatalogObject).where(
            CatalogObject.schema_id == schema.id, CatalogObject.name == table_name
        )
    )
    if result.scalar() is not None:
        raise HTTPException(
            status_code=409, detail=f"Object '{table_name}' already exists"
        )

    # Build PyArrow schema for Iceberg
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

    # Use iceberg namespace = db__schema
    iceberg_ns = f"{db_name}__{schema_name}"
    iceberg_id = f"{iceberg_ns}.{table_name}"

    # Ensure Iceberg namespace exists
    existing_ns = [ns[0] for ns in catalog.list_namespaces()]
    if iceberg_ns not in existing_ns:
        catalog.create_namespace(iceberg_ns)

    pa_schema = pa.schema(fields)
    catalog.create_table(iceberg_id, schema=pa_schema)

    # Register in metadata
    obj = CatalogObject(
        schema_id=schema.id,
        name=table_name,
        object_type="table",
        iceberg_identifier=iceberg_id,
    )
    db.add(obj)
    await db.commit()
    return {"database": db_name, "schema": schema_name, "table": table_name}


@router.post("/databases/{db_name}/schemas/{schema_name}/views")
async def create_view(
    db_name: str,
    schema_name: str,
    view_name: str,
    body: CreateView,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_auth),
):
    schema = await _get_schema(db, db_name, schema_name)

    # Check for duplicates
    result = await db.execute(
        select(CatalogObject).where(
            CatalogObject.schema_id == schema.id, CatalogObject.name == view_name
        )
    )
    if result.scalar() is not None:
        raise HTTPException(
            status_code=409, detail=f"Object '{view_name}' already exists"
        )

    obj = CatalogObject(
        schema_id=schema.id,
        name=view_name,
        object_type="view",
        view_sql=body.sql,
    )
    db.add(obj)
    await db.commit()
    return {"database": db_name, "schema": schema_name, "view": view_name}


@router.get("/databases/{db_name}/schemas/{schema_name}/objects/{obj_name}/schema")
async def get_object_schema(
    db_name: str,
    schema_name: str,
    obj_name: str,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_auth),
):
    schema = await _get_schema(db, db_name, schema_name)
    result = await db.execute(
        select(CatalogObject).where(
            CatalogObject.schema_id == schema.id, CatalogObject.name == obj_name
        )
    )
    obj = result.scalar()
    if obj is None:
        raise HTTPException(status_code=404, detail=f"Object '{obj_name}' not found")

    if obj.object_type == "table" and obj.iceberg_identifier:
        try:
            tbl = catalog.load_table(obj.iceberg_identifier)
        except Exception as e:
            raise HTTPException(status_code=404, detail=str(e))
        tbl_schema = tbl.schema()
        return {
            "type": "table",
            "columns": [
                {
                    "name": field.name,
                    "type": str(field.field_type),
                    "required": field.required,
                }
                for field in tbl_schema.fields
            ],
        }
    elif obj.object_type == "view":
        return {"type": "view", "sql": obj.view_sql, "columns": []}

    raise HTTPException(status_code=400, detail=f"Unknown object type: {obj.object_type}")


@router.delete("/databases/{db_name}/schemas/{schema_name}/objects/{obj_name}")
async def delete_object(
    db_name: str,
    schema_name: str,
    obj_name: str,
    db: AsyncSession = Depends(get_db),
    _user: dict = Depends(require_auth),
):
    schema = await _get_schema(db, db_name, schema_name)
    result = await db.execute(
        select(CatalogObject).where(
            CatalogObject.schema_id == schema.id, CatalogObject.name == obj_name
        )
    )
    obj = result.scalar()
    if obj is None:
        raise HTTPException(status_code=404, detail=f"Object '{obj_name}' not found")

    # If it's an Iceberg table, drop from catalog too
    if obj.object_type == "table" and obj.iceberg_identifier:
        try:
            catalog.drop_table(obj.iceberg_identifier)
        except Exception:
            pass  # Table may already be gone from Iceberg

    await db.delete(obj)
    await db.commit()
    return {"deleted": obj_name}
