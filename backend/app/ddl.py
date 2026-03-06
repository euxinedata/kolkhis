"""DDL statement handler — intercepts CREATE/DROP DATABASE/SCHEMA and routes to PyIceberg/Lakekeeper."""

import re
import logging

import httpx
from pyiceberg.exceptions import NoSuchNamespaceError, NoSuchTableError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import (
    LAKEKEEPER_URL, S3_BUCKET_NAME, S3_INTERNAL_ENDPOINT,
    S3_ACCESS_KEY, S3_SECRET_KEY, S3_REGION,
)
from app.models import OrgDatabase
from app.warehouse import get_database_catalog

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
    rf"^\s*DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?{_IDENT}\.{_IDENT}\.{_IDENT}\s*;?\s*$",
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
    r"^\s*DROP\s+(?:DATABASE|SCHEMA|TABLE)\s+(?:IF\s+EXISTS\s+)?.*'.*$",
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
        return {"op": "drop_table", "database": _extract_ident(m, 1), "schema": _extract_ident(m, 3), "name": _extract_ident(m, 5)}

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
        try:
            catalog.drop_namespace(schema_name)
        except NoSuchNamespaceError:
            raise ValueError(f"Schema '{database}.{schema_name}' not found")
        return f"Schema '{database}.{schema_name}' dropped"

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

        # Delete the Lakekeeper warehouse
        await _delete_lakekeeper_warehouse(org_db.lakekeeper_warehouse)
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
