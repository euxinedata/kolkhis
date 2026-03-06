"""Structural verification tests for DDL detection and result writing.

Tests detect_ddl() return shapes and _write_result() parquet output.
Pure unit tests — no external services needed.
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

    def test_create_schema_shape(self):
        result = detect_ddl("CREATE SCHEMA dev.myschema")
        assert result == {"op": "create_schema", "database": "dev", "name": "myschema"}
        assert set(result.keys()) == {"op", "database", "name"}

    def test_create_schema_quoted(self):
        result = detect_ddl('CREATE SCHEMA "dev"."myschema"')
        assert result == {"op": "create_schema", "database": "dev", "name": "myschema"}

    def test_drop_database_shape(self):
        result = detect_ddl("DROP DATABASE mydb")
        assert result == {"op": "drop_database", "name": "mydb"}
        assert set(result.keys()) == {"op", "name"}

    def test_drop_schema_shape(self):
        result = detect_ddl("DROP SCHEMA dev.myschema")
        assert result == {"op": "drop_schema", "database": "dev", "name": "myschema"}
        assert set(result.keys()) == {"op", "database", "name"}

    def test_drop_table_shape(self):
        result = detect_ddl("DROP TABLE dev.myschema.mytable")
        assert result == {
            "op": "drop_table",
            "database": "dev",
            "schema": "myschema",
            "name": "mytable",
        }
        assert set(result.keys()) == {"op", "database", "schema", "name"}

    def test_drop_view_shape(self):
        result = detect_ddl("DROP VIEW dev.myschema.myview")
        assert result == {
            "op": "drop_view",
            "database": "dev",
            "schema": "myschema",
            "name": "myview",
        }
        assert set(result.keys()) == {"op", "database", "schema", "name"}

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

    def test_create_view_shape(self):
        result = detect_ddl("CREATE VIEW dev.myschema.myview AS SELECT 1 AS x")
        assert result["op"] == "create_view"
        assert result["database"] == "dev"
        assert result["schema"] == "myschema"
        assert result["name"] == "myview"
        assert result["view_sql"] == "SELECT 1 AS x"
        assert result["or_replace"] is False
        assert set(result.keys()) == {"op", "database", "schema", "name", "view_sql", "or_replace"}

    def test_create_or_replace_view_shape(self):
        result = detect_ddl("CREATE OR REPLACE VIEW dev.myschema.myview AS SELECT 1")
        assert result["op"] == "create_view"
        assert result["or_replace"] is True
        assert result["view_sql"] == "SELECT 1"

    def test_create_view_quoted(self):
        result = detect_ddl('CREATE VIEW "dev"."myschema"."myview" AS SELECT 1')
        assert result["op"] == "create_view"
        assert result["database"] == "dev"
        assert result["schema"] == "myschema"
        assert result["name"] == "myview"

    def test_create_view_multiline_body(self):
        sql = "CREATE VIEW dev.s.v AS\nSELECT a, b\nFROM t\nWHERE x > 1"
        result = detect_ddl(sql)
        assert result["op"] == "create_view"
        assert result["view_sql"] == "SELECT a, b\nFROM t\nWHERE x > 1"

    def test_create_view_with_semicolon(self):
        result = detect_ddl("CREATE VIEW dev.s.v AS SELECT 1;")
        assert result["view_sql"] == "SELECT 1"

    def test_create_view_with_parens(self):
        result = detect_ddl("CREATE VIEW dev.s.v AS (SELECT 1)")
        assert result["view_sql"] == "(SELECT 1)"

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
