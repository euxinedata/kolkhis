"""Structural verification tests for DDL detection and result writing.

Tests detect_ddl() return shapes and _write_result() parquet output.
Pure unit tests — no external services needed.

Note: Only CREATE/DROP DATABASE, RENAME DATABASE, and SHOW commands are
intercepted as DDL. All other SQL (CREATE VIEW, DROP TABLE, etc.) flows
directly to the DuckLake worker.
"""

import os
import sys

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.ddl import detect_ddl, _write_result


class TestDetectDdlReturnShapes:
    """Exact dict keys and values for every DDL pattern."""

    def test_create_database_shape(self):
        result = detect_ddl("CREATE DATABASE mydb")
        assert result == {"op": "create_database", "name": "mydb"}
        assert set(result.keys()) == {"op", "name"}

    def test_create_database_quoted(self):
        result = detect_ddl('CREATE DATABASE "mydb"')
        assert result == {"op": "create_database", "name": "mydb"}

    def test_create_database_if_not_exists(self):
        result = detect_ddl("CREATE DATABASE IF NOT EXISTS mydb")
        assert result["op"] == "create_database"
        assert result["name"] == "mydb"

    def test_drop_database_shape(self):
        result = detect_ddl("DROP DATABASE mydb")
        assert result == {"op": "drop_database", "name": "mydb"}
        assert set(result.keys()) == {"op", "name"}

    # Operations that are NOT intercepted (forwarded to DuckLake worker)
    def test_create_schema_not_intercepted(self):
        assert detect_ddl("CREATE SCHEMA dev.myschema") is None

    def test_drop_schema_not_intercepted(self):
        assert detect_ddl("DROP SCHEMA dev.myschema") is None

    def test_drop_table_not_intercepted(self):
        assert detect_ddl("DROP TABLE dev.myschema.mytable") is None

    def test_drop_view_not_intercepted(self):
        assert detect_ddl("DROP VIEW dev.myschema.myview") is None

    def test_create_view_not_intercepted(self):
        assert detect_ddl("CREATE VIEW dev.myschema.myview AS SELECT 1 AS x") is None

    def test_create_or_replace_view_not_intercepted(self):
        assert detect_ddl("CREATE OR REPLACE VIEW dev.myschema.myview AS SELECT 1") is None

    # SHOW commands
    def test_show_databases_shape(self):
        result = detect_ddl("SHOW DATABASES")
        assert result == {"op": "show_databases"}
        assert set(result.keys()) == {"op"}

    def test_show_schemas_shape(self):
        result = detect_ddl("SHOW SCHEMAS IN dev")
        assert result == {"op": "show_schemas", "database": "dev"}
        assert set(result.keys()) == {"op", "database"}

    def test_show_tables_shape(self):
        result = detect_ddl("SHOW TABLES IN dev.myschema")
        assert result == {"op": "show_tables", "database": "dev", "schema": "myschema"}
        assert set(result.keys()) == {"op", "database", "schema"}

    # ALTER DATABASE RENAME (the only rename intercepted)
    def test_rename_database_shape(self):
        result = detect_ddl("ALTER DATABASE mydb RENAME TO newdb")
        assert result == {"op": "rename_database", "name": "mydb", "new_name": "newdb"}

    def test_rename_database_quoted(self):
        result = detect_ddl('ALTER DATABASE "mydb" RENAME TO "newdb"')
        assert result == {"op": "rename_database", "name": "mydb", "new_name": "newdb"}

    def test_rename_database_case_insensitive(self):
        result = detect_ddl("alter database MyDb rename to NewDb")
        assert result["op"] == "rename_database"
        assert result["name"] == "MyDb"
        assert result["new_name"] == "NewDb"

    # Rename SCHEMA/TABLE/VIEW — NOT intercepted (forwarded to worker)
    def test_rename_schema_not_intercepted(self):
        assert detect_ddl("ALTER SCHEMA dev.myschema RENAME TO newschema") is None

    def test_rename_table_not_intercepted(self):
        assert detect_ddl("ALTER TABLE dev.myschema.mytable RENAME TO newtable") is None

    def test_rename_view_not_intercepted(self):
        assert detect_ddl("ALTER VIEW dev.myschema.myview RENAME TO newview") is None

    def test_non_ddl_returns_none(self):
        assert detect_ddl("SELECT 1") is None

    def test_ctas_returns_none(self):
        assert detect_ddl("CREATE TABLE t AS SELECT 1") is None

    def test_insert_returns_none(self):
        assert detect_ddl("INSERT INTO t VALUES (1)") is None

    def test_single_quoted_identifiers_raise(self):
        with pytest.raises(ValueError, match="Single quotes"):
            detect_ddl("CREATE DATABASE 'x'")

    def test_drop_single_quoted_raises(self):
        with pytest.raises(ValueError, match="Single quotes"):
            detect_ddl("DROP TABLE 'x'.'y'.'z'")


class TestWriteResultParquetStructure:
    """_write_result() output verification."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path, monkeypatch):
        self.results_path = str(tmp_path)
        monkeypatch.setattr("app.ddl.RESULTS_PATH", self.results_path)

    def _read_result(self, job_id):
        return pq.read_table(os.path.join(self.results_path, f"{job_id}.parquet"))

    def test_show_databases_parquet_structure(self):
        row_count = _write_result("job1", {"database_name": ["dev", "prod"]})
        table = self._read_result("job1")
        assert table.schema.names == ["database_name"]
        assert table.schema.field("database_name").type == pa.utf8()
        assert table.column("database_name").to_pylist() == ["dev", "prod"]
        assert row_count == 2

    def test_show_schemas_parquet_structure(self):
        row_count = _write_result("job2", {"schema_name": ["public", "staging"]})
        table = self._read_result("job2")
        assert table.schema.names == ["schema_name"]
        assert table.schema.field("schema_name").type == pa.utf8()
        assert row_count == 2

    def test_show_tables_parquet_structure(self):
        row_count = _write_result(
            "job3",
            {"table_name": ["users", "orders"], "table_type": ["TABLE", "TABLE"]},
        )
        table = self._read_result("job3")
        assert table.schema.names == ["table_name", "table_type"]
        assert table.schema.field("table_name").type == pa.utf8()
        assert table.schema.field("table_type").type == pa.utf8()
        assert row_count == 2

    def test_empty_result(self):
        row_count = _write_result("job4", {"database_name": []})
        table = self._read_result("job4")
        assert table.num_rows == 0
        assert table.schema.names == ["database_name"]

    def test_returns_row_count(self):
        row_count = _write_result("job5", {"schema_name": ["a", "b", "c"]})
        assert row_count == 3
