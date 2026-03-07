"""DDL statement handler — intercepts CREATE/DROP DATABASE/SCHEMA and routes to DuckLake/PostgreSQL."""

import re
import logging

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import RESULTS_PATH
from app.models import OrgDatabase
from app.warehouse import ducklake_metadata_schema, ducklake_data_path

logger = logging.getLogger(__name__)

# SQL identifier: unquoted word or double-quoted word
_IDENT = r"""(?:(\w+)|"(\w+)")"""

# Patterns for DDL detection
_CREATE_DATABASE_RE = re.compile(
    rf"^\s*CREATE\s+DATABASE\s+(?:IF\s+NOT\s+EXISTS\s+)?{_IDENT}\s*;?\s*$",
    re.IGNORECASE,
)
_DROP_DATABASE_RE = re.compile(
    rf"^\s*DROP\s+DATABASE\s+(?:IF\s+EXISTS\s+)?{_IDENT}\s*;?\s*$",
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

_ALTER_DATABASE_RENAME_RE = re.compile(
    rf"^\s*ALTER\s+DATABASE\s+{_IDENT}\s+RENAME\s+TO\s+{_IDENT}\s*;?\s*$",
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

    m = _DROP_DATABASE_RE.match(sql)
    if m:
        return {"op": "drop_database", "name": _extract_ident(m, 1)}

    # SHOW commands
    if _SHOW_DATABASES_RE.match(sql):
        return {"op": "show_databases"}

    m = _SHOW_SCHEMAS_RE.match(sql)
    if m:
        return {"op": "show_schemas", "database": _extract_ident(m, 1)}

    m = _SHOW_TABLES_RE.match(sql)
    if m:
        return {"op": "show_tables", "database": _extract_ident(m, 1), "schema": _extract_ident(m, 3)}

    # ALTER DATABASE RENAME — modifies OrgDatabase record
    m = _ALTER_DATABASE_RENAME_RE.match(sql)
    if m:
        return {"op": "rename_database", "name": _extract_ident(m, 1), "new_name": _extract_ident(m, 3)}

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

        data_path = ducklake_data_path(org_id, name)
        metadata_schema = ducklake_metadata_schema(org_id, name)
        org_db = OrgDatabase(
            org_id=org_id, name=name,
            data_path=data_path, metadata_schema=metadata_schema,
        )
        db.add(org_db)
        await db.commit()
        return f"Database '{name}' created"

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

        # Rename the PostgreSQL metadata schema, but keep the same data_path
        # (S3 data stays in place — only the logical name changes)
        new_metadata_schema = ducklake_metadata_schema(org_id, new_name)
        old_schema = org_db.metadata_schema
        await db.execute(text(
            f'ALTER SCHEMA "{old_schema}" RENAME TO "{new_metadata_schema}"'
        ))

        org_db.name = new_name
        # data_path stays the same — S3 files don't move
        org_db.metadata_schema = new_metadata_schema
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

        # Drop the DuckLake metadata schema from PostgreSQL
        metadata_schema = org_db.metadata_schema
        await db.execute(text(f'DROP SCHEMA IF EXISTS "{metadata_schema}" CASCADE'))

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

        # Check if metadata schema exists yet (created on first DuckLake ATTACH)
        schema_exists = await db.execute(
            text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :name"),
            {"name": org_db.metadata_schema},
        )
        if schema_exists.scalar() is None:
            return _write_result(job_id, {"schema_name": []})

        # Query DuckLake metadata tables in PostgreSQL directly
        schema_result = await db.execute(
            text(
                f'SELECT schema_name FROM "{org_db.metadata_schema}".ducklake_schema '
                f"WHERE end_snapshot IS NULL ORDER BY schema_name"
            )
        )
        names = [row[0] for row in schema_result.all()]
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

        meta = org_db.metadata_schema

        # Check if metadata schema exists yet (created on first DuckLake ATTACH)
        schema_exists = await db.execute(
            text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :name"),
            {"name": meta},
        )
        if schema_exists.scalar() is None:
            return _write_result(job_id, {"table_name": [], "table_type": []})

        # Query tables from DuckLake metadata
        table_result = await db.execute(text(
            f'SELECT t.table_name FROM "{meta}".ducklake_table t '
            f'JOIN "{meta}".ducklake_schema s ON t.schema_id = s.schema_id '
            f"WHERE s.schema_name = :schema_name AND s.end_snapshot IS NULL AND t.end_snapshot IS NULL "
            f"ORDER BY t.table_name"
        ), {"schema_name": schema_name})
        table_names = [row[0] for row in table_result.all()]

        # Query views from DuckLake metadata
        try:
            view_result = await db.execute(text(
                f'SELECT v.view_name FROM "{meta}".ducklake_view v '
                f'JOIN "{meta}".ducklake_schema s ON v.schema_id = s.schema_id '
                f"WHERE s.schema_name = :schema_name AND s.end_snapshot IS NULL AND v.end_snapshot IS NULL "
                f"ORDER BY v.view_name"
            ), {"schema_name": schema_name})
            view_names = [row[0] for row in view_result.all()]
        except Exception:
            view_names = []

        names = []
        types = []
        for n in table_names:
            names.append(n)
            types.append("TABLE")
        for n in view_names:
            names.append(n)
            types.append("VIEW")
        return _write_result(job_id, {"table_name": names, "table_type": types})

    else:
        raise ValueError(f"Unsupported SHOW operation: {op}")
