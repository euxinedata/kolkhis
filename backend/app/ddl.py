"""DDL statement handler — intercepts CREATE/DROP DATABASE/SCHEMA and routes to PyIceberg/Lakekeeper."""

import re
import logging

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
from pyiceberg.exceptions import NoSuchNamespaceError, NoSuchTableError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import (
    LAKEKEEPER_URL, RESULTS_PATH, S3_BUCKET_NAME, S3_INTERNAL_ENDPOINT,
    S3_ACCESS_KEY, S3_SECRET_KEY, S3_REGION,
)
from app.models import OrgDatabase, OrgView
from app.warehouse import get_database_catalog, invalidate_catalog_cache

logger = logging.getLogger(__name__)

# SQL identifier: unquoted word or double-quoted word
_IDENT = r"""(?:(\w+)|"(\w+)")"""

# Patterns for DDL detection
_CREATE_DATABASE_RE = re.compile(
    rf"^\s*CREATE\s+DATABASE\s+(?:IF\s+NOT\s+EXISTS\s+)?{_IDENT}\s*;?\s*$",
    re.IGNORECASE,
)
_CREATE_SCHEMA_RE = re.compile(
    rf"^\s*CREATE\s+SCHEMA\s+(?:IF\s+NOT\s+EXISTS\s+)?{_IDENT}\.{_IDENT}\s*;?\s*$",
    re.IGNORECASE,
)

_CREATE_VIEW_RE = re.compile(
    rf"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+{_IDENT}\.{_IDENT}\.{_IDENT}\s+AS\s+",
    re.IGNORECASE | re.DOTALL,
)

# DROP patterns
_DROP_DATABASE_RE = re.compile(
    rf"^\s*DROP\s+DATABASE\s+(?:IF\s+EXISTS\s+)?{_IDENT}\s*;?\s*$",
    re.IGNORECASE,
)
_DROP_SCHEMA_RE = re.compile(
    rf"^\s*DROP\s+SCHEMA\s+(?:IF\s+EXISTS\s+)?{_IDENT}\.{_IDENT}\s*;?\s*$",
    re.IGNORECASE,
)
_DROP_TABLE_RE = re.compile(
    rf"^\s*DROP\s+(TABLE|VIEW)\s+(?:IF\s+EXISTS\s+)?{_IDENT}\.{_IDENT}\.{_IDENT}\s*;?\s*$",
    re.IGNORECASE,
)

# Detect single-quoted identifiers to give a helpful error
_CREATE_DATABASE_BAD_QUOTES_RE = re.compile(
    r"^\s*CREATE\s+DATABASE\s+(?:IF\s+NOT\s+EXISTS\s+)?'(\w+)'\s*;?\s*$",
    re.IGNORECASE,
)
_CREATE_SCHEMA_BAD_QUOTES_RE = re.compile(
    r"^\s*CREATE\s+SCHEMA\s+(?:IF\s+NOT\s+EXISTS\s+)?'?\w+'?\.?'?\w+'?\s*;?\s*$",
    re.IGNORECASE,
)
_DROP_BAD_QUOTES_RE = re.compile(
    r"^\s*DROP\s+(?:DATABASE|SCHEMA|TABLE|VIEW)\s+(?:IF\s+EXISTS\s+)?.*'.*$",
    re.IGNORECASE,
)

# ALTER ... RENAME TO patterns
_ALTER_DATABASE_RENAME_RE = re.compile(
    rf"^\s*ALTER\s+DATABASE\s+{_IDENT}\s+RENAME\s+TO\s+{_IDENT}\s*;?\s*$",
    re.IGNORECASE,
)
_ALTER_SCHEMA_RENAME_RE = re.compile(
    rf"^\s*ALTER\s+SCHEMA\s+{_IDENT}\.{_IDENT}\s+RENAME\s+TO\s+{_IDENT}\s*;?\s*$",
    re.IGNORECASE,
)
_ALTER_TABLE_RENAME_RE = re.compile(
    rf"^\s*ALTER\s+TABLE\s+{_IDENT}\.{_IDENT}\.{_IDENT}\s+RENAME\s+TO\s+{_IDENT}\s*;?\s*$",
    re.IGNORECASE,
)
_ALTER_VIEW_RENAME_RE = re.compile(
    rf"^\s*ALTER\s+VIEW\s+{_IDENT}\.{_IDENT}\.{_IDENT}\s+RENAME\s+TO\s+{_IDENT}\s*;?\s*$",
    re.IGNORECASE,
)

# SHOW patterns
_SHOW_DATABASES_RE = re.compile(
    r"^\s*SHOW\s+DATABASES\s*;?\s*$",
    re.IGNORECASE,
)
_SHOW_SCHEMAS_RE = re.compile(
    rf"^\s*SHOW\s+SCHEMAS\s+IN\s+{_IDENT}\s*;?\s*$",
    re.IGNORECASE,
)
_SHOW_TABLES_RE = re.compile(
    rf"^\s*SHOW\s+TABLES\s+IN\s+{_IDENT}\.{_IDENT}\s*;?\s*$",
    re.IGNORECASE,
)


def _extract_ident(m: re.Match, start: int) -> str:
    """Extract identifier from a match with 2 alternatives (unquoted, double-quoted)."""
    return m.group(start) or m.group(start + 1)


def detect_ddl(sql: str) -> dict | None:
    """Return a dict describing the DDL operation, or None if not DDL."""
    m = _CREATE_DATABASE_RE.match(sql)
    if m:
        return {"op": "create_database", "name": _extract_ident(m, 1)}

    m = _CREATE_SCHEMA_RE.match(sql)
    if m:
        return {"op": "create_schema", "database": _extract_ident(m, 1), "name": _extract_ident(m, 3)}

    m = _DROP_DATABASE_RE.match(sql)
    if m:
        return {"op": "drop_database", "name": _extract_ident(m, 1)}

    m = _DROP_SCHEMA_RE.match(sql)
    if m:
        return {"op": "drop_schema", "database": _extract_ident(m, 1), "name": _extract_ident(m, 3)}

    m = _DROP_TABLE_RE.match(sql)
    if m:
        kind = m.group(1).lower()  # "table" or "view"
        return {"op": f"drop_{kind}", "database": _extract_ident(m, 2), "schema": _extract_ident(m, 4), "name": _extract_ident(m, 6)}

    # SHOW commands
    if _SHOW_DATABASES_RE.match(sql):
        return {"op": "show_databases"}

    m = _SHOW_SCHEMAS_RE.match(sql)
    if m:
        return {"op": "show_schemas", "database": _extract_ident(m, 1)}

    m = _SHOW_TABLES_RE.match(sql)
    if m:
        return {"op": "show_tables", "database": _extract_ident(m, 1), "schema": _extract_ident(m, 3)}

    # ALTER ... RENAME TO
    m = _ALTER_DATABASE_RENAME_RE.match(sql)
    if m:
        return {"op": "rename_database", "name": _extract_ident(m, 1), "new_name": _extract_ident(m, 3)}

    m = _ALTER_SCHEMA_RENAME_RE.match(sql)
    if m:
        return {"op": "rename_schema", "database": _extract_ident(m, 1), "name": _extract_ident(m, 3), "new_name": _extract_ident(m, 5)}

    m = _ALTER_TABLE_RENAME_RE.match(sql)
    if m:
        return {"op": "rename_table", "database": _extract_ident(m, 1), "schema": _extract_ident(m, 3), "name": _extract_ident(m, 5), "new_name": _extract_ident(m, 7)}

    m = _ALTER_VIEW_RENAME_RE.match(sql)
    if m:
        return {"op": "rename_view", "database": _extract_ident(m, 1), "schema": _extract_ident(m, 3), "name": _extract_ident(m, 5), "new_name": _extract_ident(m, 7)}

    m = _CREATE_VIEW_RE.match(sql)
    if m:
        or_replace = bool(re.match(r"^\s*CREATE\s+OR\s+REPLACE\s+", sql, re.IGNORECASE))
        database = _extract_ident(m, 1)
        schema = _extract_ident(m, 3)
        name = _extract_ident(m, 5)
        # Extract the SQL body after "AS "
        body_start = m.end()
        view_sql = sql[body_start:].strip().rstrip(";").strip()
        return {"op": "create_view", "database": database, "schema": schema, "name": name, "view_sql": view_sql, "or_replace": or_replace}

    # Check for single-quoted identifiers and return a helpful error
    if _CREATE_DATABASE_BAD_QUOTES_RE.match(sql):
        raise ValueError(
            "Single quotes are for string literals. Use double quotes for identifiers: "
            "CREATE DATABASE \"name\" or CREATE DATABASE name"
        )
    if _CREATE_SCHEMA_BAD_QUOTES_RE.match(sql) and "'" in sql:
        raise ValueError(
            "Single quotes are for string literals. Use double quotes for identifiers: "
            "CREATE SCHEMA \"database\".\"schema\" or CREATE SCHEMA database.schema"
        )
    if _DROP_BAD_QUOTES_RE.match(sql):
        raise ValueError(
            "Single quotes are for string literals. Use double quotes for identifiers, "
            "e.g.: DROP TABLE database.schema.table"
        )

    return None


async def _create_lakekeeper_warehouse(org_id: str, db_name: str) -> str:
    """Create a Lakekeeper warehouse for an org database."""
    warehouse_name = f"{org_id}-{db_name}"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{LAKEKEEPER_URL}/management/v1/warehouse",
            headers={
                "Content-Type": "application/json",
                "X-Project-Id": "00000000-0000-0000-0000-000000000000",
            },
            json={
                "warehouse-name": warehouse_name,
                "storage-profile": {
                    "type": "s3",
                    "bucket": S3_BUCKET_NAME,
                    "key-prefix": f"{org_id}/{db_name}",
                    "region": S3_REGION,
                    "flavor": "s3-compat",
                    "endpoint": S3_INTERNAL_ENDPOINT,
                    "path-style-access": True,
                    "sts-enabled": False,
                    "remote-signing-enabled": False,
                },
                "storage-credential": {
                    "type": "s3",
                    "credential-type": "access-key",
                    "aws-access-key-id": S3_ACCESS_KEY,
                    "aws-secret-access-key": S3_SECRET_KEY,
                },
            },
        )
        if resp.status_code == 409:
            raise ValueError(f"Database '{db_name}' already exists")
        if resp.status_code != 201:
            raise Exception(f"Failed to create database: {resp.status_code} {resp.text}")
    return warehouse_name


async def _rename_lakekeeper_warehouse(warehouse_name: str, new_warehouse_name: str) -> None:
    """Rename a Lakekeeper warehouse."""
    headers = {
        "Content-Type": "application/json",
        "X-Project-Id": "00000000-0000-0000-0000-000000000000",
    }
    async with httpx.AsyncClient() as client:
        # Look up warehouse ID by name
        resp = await client.get(
            f"{LAKEKEEPER_URL}/management/v1/warehouse",
            params={"warehouse-name": warehouse_name},
            headers=headers,
        )
        if resp.status_code != 200:
            raise Exception(f"Failed to list warehouses: {resp.status_code} {resp.text}")
        warehouses = resp.json().get("warehouses", [])
        match = next((w for w in warehouses if w["name"] == warehouse_name), None)
        if match is None:
            raise ValueError(f"Lakekeeper warehouse '{warehouse_name}' not found")
        warehouse_id = match["warehouse-id"]

        # Rename
        resp = await client.post(
            f"{LAKEKEEPER_URL}/management/v1/warehouse/{warehouse_id}/rename",
            json={"new-name": new_warehouse_name},
            headers=headers,
        )
        if resp.status_code == 409:
            raise ValueError(f"Warehouse '{new_warehouse_name}' already exists")
        if resp.status_code not in (200, 204):
            raise Exception(f"Failed to rename warehouse: {resp.status_code} {resp.text}")


async def _delete_lakekeeper_warehouse(warehouse_name: str) -> None:
    """Delete a Lakekeeper warehouse by name."""
    headers = {
        "Content-Type": "application/json",
        "X-Project-Id": "00000000-0000-0000-0000-000000000000",
    }
    async with httpx.AsyncClient() as client:
        # Look up warehouse ID by name
        resp = await client.get(
            f"{LAKEKEEPER_URL}/management/v1/warehouse",
            params={"warehouse-name": warehouse_name},
            headers=headers,
        )
        if resp.status_code != 200:
            raise Exception(f"Failed to list warehouses: {resp.status_code} {resp.text}")
        warehouses = resp.json().get("warehouses", [])
        match = next((w for w in warehouses if w["name"] == warehouse_name), None)
        if match is None:
            raise ValueError(f"Lakekeeper warehouse '{warehouse_name}' not found")
        warehouse_id = match["warehouse-id"]

        # Delete by ID
        resp = await client.delete(
            f"{LAKEKEEPER_URL}/management/v1/warehouse/{warehouse_id}",
            headers=headers,
        )
        if resp.status_code not in (200, 204):
            raise Exception(f"Failed to delete warehouse: {resp.status_code} {resp.text}")


def _drop_namespace_cascade(catalog, namespace: str) -> None:
    """Drop all tables and views in a namespace, then drop the namespace."""
    for table_id in catalog.list_tables(namespace):
        catalog.drop_table(f"{namespace}.{table_id[1]}")
    try:
        for view_id in catalog.list_views(namespace):
            catalog.drop_view(f"{namespace}.{view_id[1]}")
    except Exception:
        pass  # list_views may not be supported
    catalog.drop_namespace(namespace)


async def execute_ddl(ddl: dict, org_id: str, db: AsyncSession) -> str:
    """Execute a DDL operation. Returns a success message."""
    op = ddl["op"]

    if op == "create_database":
        name = ddl["name"]
        # Check if already exists
        result = await db.execute(
            select(OrgDatabase).where(
                OrgDatabase.org_id == org_id,
                OrgDatabase.name == name,
            )
        )
        if result.scalar_one_or_none() is not None:
            raise ValueError(f"Database '{name}' already exists")

        warehouse_name = await _create_lakekeeper_warehouse(org_id, name)
        invalidate_catalog_cache()
        org_db = OrgDatabase(org_id=org_id, name=name, lakekeeper_warehouse=warehouse_name)
        db.add(org_db)
        await db.commit()
        return f"Database '{name}' created"

    elif op == "create_schema":
        database = ddl["database"]
        schema_name = ddl["name"]
        result = await db.execute(
            select(OrgDatabase).where(
                OrgDatabase.org_id == org_id,
                OrgDatabase.name == database,
            )
        )
        org_db = result.scalar_one_or_none()
        if org_db is None:
            raise ValueError(f"Database '{database}' not found")

        catalog = get_database_catalog(org_db.lakekeeper_warehouse)
        catalog.create_namespace_if_not_exists(schema_name)
        return f"Schema '{database}.{schema_name}' created"

    elif op == "create_view":
        database = ddl["database"]
        schema_name = ddl["schema"]
        view_name = ddl["name"]
        view_sql = ddl["view_sql"]
        or_replace = ddl.get("or_replace", False)

        # Validate database exists
        result = await db.execute(
            select(OrgDatabase).where(
                OrgDatabase.org_id == org_id,
                OrgDatabase.name == database,
            )
        )
        if result.scalar_one_or_none() is None:
            raise ValueError(f"Database '{database}' not found")

        # Check for existing view
        result = await db.execute(
            select(OrgView).where(
                OrgView.org_id == org_id,
                OrgView.database == database,
                OrgView.schema_name == schema_name,
                OrgView.name == view_name,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            if not or_replace:
                raise ValueError(f"View '{database}.{schema_name}.{view_name}' already exists")
            existing.view_sql = view_sql
        else:
            db.add(OrgView(
                org_id=org_id,
                database=database,
                schema_name=schema_name,
                name=view_name,
                view_sql=view_sql,
            ))
        await db.commit()
        return f"View '{database}.{schema_name}.{view_name}' created"

    elif op == "drop_table":
        database = ddl["database"]
        schema_name = ddl["schema"]
        table_name = ddl["name"]
        result = await db.execute(
            select(OrgDatabase).where(
                OrgDatabase.org_id == org_id,
                OrgDatabase.name == database,
            )
        )
        org_db = result.scalar_one_or_none()
        if org_db is None:
            raise ValueError(f"Database '{database}' not found")

        catalog = get_database_catalog(org_db.lakekeeper_warehouse)
        table_id = f"{schema_name}.{table_name}"
        try:
            catalog.drop_table(table_id)
        except NoSuchTableError:
            raise ValueError(f"Table '{database}.{table_id}' not found")
        return f"Table '{database}.{table_id}' dropped"

    elif op == "drop_view":
        database = ddl["database"]
        schema_name = ddl["schema"]
        view_name = ddl["name"]
        result = await db.execute(
            select(OrgDatabase).where(
                OrgDatabase.org_id == org_id,
                OrgDatabase.name == database,
            )
        )
        org_db = result.scalar_one_or_none()
        if org_db is None:
            raise ValueError(f"Database '{database}' not found")

        view_id = f"{schema_name}.{view_name}"
        found = False

        # Try Lakekeeper first (views created by PyIceberg)
        catalog = get_database_catalog(org_db.lakekeeper_warehouse)
        try:
            catalog.drop_view(view_id)
            found = True
        except (NoSuchTableError, Exception):
            pass

        # Also delete from org_views (views created via our DDL handler)
        result = await db.execute(
            delete(OrgView).where(
                OrgView.org_id == org_id,
                OrgView.database == database,
                OrgView.schema_name == schema_name,
                OrgView.name == view_name,
            )
        )
        if result.rowcount > 0:
            found = True
            await db.commit()

        if not found:
            raise ValueError(f"View '{database}.{view_id}' not found")
        return f"View '{database}.{view_id}' dropped"

    elif op == "drop_schema":
        database = ddl["database"]
        schema_name = ddl["name"]
        result = await db.execute(
            select(OrgDatabase).where(
                OrgDatabase.org_id == org_id,
                OrgDatabase.name == database,
            )
        )
        org_db = result.scalar_one_or_none()
        if org_db is None:
            raise ValueError(f"Database '{database}' not found")

        catalog = get_database_catalog(org_db.lakekeeper_warehouse)
        _drop_namespace_cascade(catalog, schema_name)
        return f"Schema '{database}.{schema_name}' dropped"

    elif op == "rename_view":
        database = ddl["database"]
        schema_name = ddl["schema"]
        old_name = ddl["name"]
        new_name = ddl["new_name"]
        result = await db.execute(
            select(OrgView).where(
                OrgView.org_id == org_id,
                OrgView.database == database,
                OrgView.schema_name == schema_name,
                OrgView.name == old_name,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is None:
            raise ValueError(f"View '{database}.{schema_name}.{old_name}' not found")
        existing.name = new_name
        await db.commit()
        return f"View '{database}.{schema_name}.{old_name}' renamed to '{new_name}'"

    elif op == "rename_table":
        database = ddl["database"]
        schema_name = ddl["schema"]
        old_name = ddl["name"]
        new_name = ddl["new_name"]
        result = await db.execute(
            select(OrgDatabase).where(
                OrgDatabase.org_id == org_id,
                OrgDatabase.name == database,
            )
        )
        org_db = result.scalar_one_or_none()
        if org_db is None:
            raise ValueError(f"Database '{database}' not found")

        catalog = get_database_catalog(org_db.lakekeeper_warehouse)
        from_id = f"{schema_name}.{old_name}"
        to_id = f"{schema_name}.{new_name}"
        try:
            catalog.rename_table(from_id, to_id)
        except NoSuchTableError:
            raise ValueError(f"Table '{database}.{from_id}' not found")
        return f"Table '{database}.{from_id}' renamed to '{new_name}'"

    elif op == "rename_schema":
        database = ddl["database"]
        old_name = ddl["name"]
        new_name = ddl["new_name"]
        result = await db.execute(
            select(OrgDatabase).where(
                OrgDatabase.org_id == org_id,
                OrgDatabase.name == database,
            )
        )
        org_db = result.scalar_one_or_none()
        if org_db is None:
            raise ValueError(f"Database '{database}' not found")

        catalog = get_database_catalog(org_db.lakekeeper_warehouse)
        if not catalog.namespace_exists(old_name):
            raise ValueError(f"Schema '{database}.{old_name}' not found")

        # Create new namespace
        catalog.create_namespace(new_name)

        # Move all tables
        for table_id in catalog.list_tables(old_name):
            catalog.rename_table(
                f"{old_name}.{table_id[1]}",
                f"{new_name}.{table_id[1]}",
            )

        # Move org_views
        from sqlalchemy import update
        await db.execute(
            update(OrgView).where(
                OrgView.org_id == org_id,
                OrgView.database == database,
                OrgView.schema_name == old_name,
            ).values(schema_name=new_name)
        )
        await db.commit()

        # Drop old namespace (should be empty now)
        catalog.drop_namespace(old_name)
        return f"Schema '{database}.{old_name}' renamed to '{new_name}'"

    elif op == "rename_database":
        old_name = ddl["name"]
        new_name = ddl["new_name"]

        # Check source exists
        result = await db.execute(
            select(OrgDatabase).where(
                OrgDatabase.org_id == org_id,
                OrgDatabase.name == old_name,
            )
        )
        org_db = result.scalar_one_or_none()
        if org_db is None:
            raise ValueError(f"Database '{old_name}' not found")

        # Check target doesn't exist
        result = await db.execute(
            select(OrgDatabase).where(
                OrgDatabase.org_id == org_id,
                OrgDatabase.name == new_name,
            )
        )
        if result.scalar_one_or_none() is not None:
            raise ValueError(f"Database '{new_name}' already exists")

        # Rename Lakekeeper warehouse
        old_warehouse = org_db.lakekeeper_warehouse
        new_warehouse = f"{org_id}-{new_name}"
        await _rename_lakekeeper_warehouse(old_warehouse, new_warehouse)
        invalidate_catalog_cache()

        # Update OrgDatabase record
        org_db.name = new_name
        org_db.lakekeeper_warehouse = new_warehouse

        # Update org_views that reference this database
        from sqlalchemy import update
        await db.execute(
            update(OrgView).where(
                OrgView.org_id == org_id,
                OrgView.database == old_name,
            ).values(database=new_name)
        )
        await db.commit()
        return f"Database '{old_name}' renamed to '{new_name}'"

    elif op == "drop_database":
        name = ddl["name"]
        result = await db.execute(
            select(OrgDatabase).where(
                OrgDatabase.org_id == org_id,
                OrgDatabase.name == name,
            )
        )
        org_db = result.scalar_one_or_none()
        if org_db is None:
            raise ValueError(f"Database '{name}' not found")

        # Cascade: drop all schemas (which drops all tables/views)
        catalog = get_database_catalog(org_db.lakekeeper_warehouse)
        for ns in catalog.list_namespaces():
            _drop_namespace_cascade(catalog, ns[0])

        # Delete the Lakekeeper warehouse
        await _delete_lakekeeper_warehouse(org_db.lakekeeper_warehouse)
        invalidate_catalog_cache()
        # Remove OrgDatabase record
        await db.execute(
            delete(OrgDatabase).where(
                OrgDatabase.org_id == org_id,
                OrgDatabase.name == name,
            )
        )
        await db.commit()
        return f"Database '{name}' dropped"

    else:
        raise ValueError(f"Unsupported DDL operation: {op}")


def _write_result(job_id: str, columns: dict[str, list]) -> int:
    """Write a result set as parquet and return row count."""
    import os
    os.makedirs(RESULTS_PATH, exist_ok=True)
    table = pa.table(columns)
    pq.write_table(table, os.path.join(RESULTS_PATH, f"{job_id}.parquet"))
    return table.num_rows


async def execute_show(ddl: dict, org_id: str, job_id: str, db: AsyncSession) -> int:
    """Execute a SHOW command, write results as parquet, return row count."""
    op = ddl["op"]

    if op == "show_databases":
        result = await db.execute(
            select(OrgDatabase.name).where(OrgDatabase.org_id == org_id).order_by(OrgDatabase.name)
        )
        names = [row[0] for row in result.all()]
        return _write_result(job_id, {"database_name": names})

    elif op == "show_schemas":
        database = ddl["database"]
        result = await db.execute(
            select(OrgDatabase).where(
                OrgDatabase.org_id == org_id,
                OrgDatabase.name == database,
            )
        )
        org_db = result.scalar_one_or_none()
        if org_db is None:
            raise ValueError(f"Database '{database}' not found")

        catalog = get_database_catalog(org_db.lakekeeper_warehouse)
        namespaces = catalog.list_namespaces()
        names = sorted(ns[0] for ns in namespaces)
        return _write_result(job_id, {"schema_name": names})

    elif op == "show_tables":
        database = ddl["database"]
        schema_name = ddl["schema"]
        result = await db.execute(
            select(OrgDatabase).where(
                OrgDatabase.org_id == org_id,
                OrgDatabase.name == database,
            )
        )
        org_db = result.scalar_one_or_none()
        if org_db is None:
            raise ValueError(f"Database '{database}' not found")

        catalog = get_database_catalog(org_db.lakekeeper_warehouse)
        tables = catalog.list_tables(schema_name)
        table_names = sorted(t[1] for t in tables)
        # Also list views
        try:
            views = catalog.list_views(schema_name)
            view_names = sorted(v[1] for v in views)
        except Exception:
            view_names = []

        # Also list views from org_views
        result = await db.execute(
            select(OrgView.name).where(
                OrgView.org_id == org_id,
                OrgView.database == database,
                OrgView.schema_name == schema_name,
            )
        )
        org_view_names = sorted(row[0] for row in result.all())

        names = []
        types = []
        for n in table_names:
            names.append(n)
            types.append("TABLE")
        all_view_names = sorted(set(view_names) | set(org_view_names))
        for n in all_view_names:
            names.append(n)
            types.append("VIEW")
        return _write_result(job_id, {"table_name": names, "table_type": types})

    else:
        raise ValueError(f"Unsupported SHOW operation: {op}")
