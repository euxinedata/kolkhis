"""Structural verification tests for execute_query() parquet output.

Verifies exact column names, types, values, and row counts of parquet files
produced by the query path (CTAS) and command path (direct execution).
No external services needed — uses in-memory DuckDB with empty catalog_objects.
"""

import os
import sys
import uuid

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from executor import execute_query


class TestQueryParquetStructure:
    """SELECT-path parquet verification."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.result_dir = tmp_path
        self.s3_kwargs = dict(
            s3_endpoint="http://localhost:9000",
            s3_access_key="test",
            s3_secret_key="test",
            s3_region="us-east-1",
        )

    def _run(self, sql, max_rows=1000):
        job_id = str(uuid.uuid4())
        path = str(self.result_dir / f"{job_id}.parquet")
        row_count = execute_query(
            job_id=job_id,
            sql=sql,
            lakekeeper_url="http://localhost:8181", databases=[],
            result_path=path,
            max_result_rows=max_rows,
            **self.s3_kwargs,
        )
        table = pq.read_table(path)
        return row_count, table

    def test_select_parquet_column_names(self):
        _, table = self._run("SELECT 1 AS x, 'hello' AS y, true AS z")
        assert table.schema.names == ["x", "y", "z"]

    def test_select_parquet_column_types(self):
        _, table = self._run(
            "SELECT 42 AS int_col, 3.14::DOUBLE AS dbl_col, 'text' AS str_col, true AS bool_col"
        )
        assert pa.types.is_integer(table.schema.field("int_col").type)
        assert pa.types.is_floating(table.schema.field("dbl_col").type)
        assert table.schema.field("str_col").type == pa.utf8()
        assert table.schema.field("bool_col").type == pa.bool_()

    def test_select_parquet_values(self):
        _, table = self._run("SELECT 42 AS answer, 'hello' AS greeting")
        d = table.to_pydict()
        assert d["answer"] == [42]
        assert d["greeting"] == ["hello"]

    def test_select_multi_row_parquet(self):
        _, table = self._run("SELECT * FROM generate_series(1, 3) AS t(x)")
        assert table.num_rows == 3
        assert table.column("x").to_pylist() == [1, 2, 3]

    def test_select_empty_result(self):
        _, table = self._run("SELECT 1 AS x, 'y' AS name WHERE false")
        assert table.num_rows == 0
        assert table.schema.names == ["x", "name"]

    def test_select_null_values_in_parquet(self):
        _, table = self._run("SELECT NULL::INTEGER AS val")
        assert table.column("val").to_pylist() == [None]


class TestCommandParquetStructure:
    """Command-path parquet verification."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.result_dir = tmp_path
        self.s3_kwargs = dict(
            s3_endpoint="http://localhost:9000",
            s3_access_key="test",
            s3_secret_key="test",
            s3_region="us-east-1",
        )

    def _run(self, sql):
        job_id = str(uuid.uuid4())
        path = str(self.result_dir / f"{job_id}.parquet")
        row_count = execute_query(
            job_id=job_id,
            sql=sql,
            lakekeeper_url="http://localhost:8181", databases=[],
            result_path=path,
            max_result_rows=1000,
            **self.s3_kwargs,
        )
        table = pq.read_table(path)
        return row_count, table

    def test_command_parquet_has_exactly_two_columns(self):
        _, table = self._run("CREATE TABLE t (id INT)")
        assert table.schema.names == ["status", "rows_affected"]

    def test_command_status_column_type(self):
        _, table = self._run("CREATE TABLE t (id INT)")
        assert table.schema.field("status").type == pa.utf8()

    def test_command_rows_affected_column_type(self):
        _, table = self._run("CREATE TABLE t (id INT)")
        assert pa.types.is_integer(table.schema.field("rows_affected").type)

    def test_command_status_value_is_ok(self):
        _, table = self._run("CREATE TABLE t (id INT)")
        assert table.column("status").to_pylist() == ["OK"]

    def test_command_rows_affected_for_ddl(self):
        _, table = self._run("CREATE TABLE t (id INT)")
        assert table.column("rows_affected").to_pylist() == [-1]

    def test_command_rows_affected_for_ctas(self):
        _, table = self._run(
            "CREATE TABLE t AS SELECT * FROM generate_series(1, 5) AS t(x)"
        )
        assert table.column("rows_affected").to_pylist() == [5]

    def test_command_set_rows_affected(self):
        _, table = self._run("SET threads TO 2")
        assert table.column("rows_affected").to_pylist() == [-1]

    def test_command_parquet_always_one_row(self):
        row_count, table = self._run("CREATE TABLE t (id INT)")
        assert table.num_rows == 1
        assert row_count == 1


class TestSetupConnectionEphemeral:
    """Proves the job path (setup_connection) doesn't persist tables."""

    def test_create_table_does_not_persist_across_connections(self, tmp_path):
        """CTAS succeeds on one connection, next connection can't find table."""
        s3_kwargs = dict(
            s3_endpoint="http://localhost:9000",
            s3_access_key="test",
            s3_secret_key="test",
            s3_region="us-east-1",
        )

        # First connection: create a table
        job_id1 = str(uuid.uuid4())
        path1 = str(tmp_path / f"{job_id1}.parquet")
        row_count = execute_query(
            job_id=job_id1, sql="CREATE TABLE ephemeral_test AS SELECT 1 AS x",
            lakekeeper_url="http://localhost:8181", databases=[], result_path=path1, max_result_rows=1000,
            **s3_kwargs,
        )
        assert row_count == 1

        # Second connection: try to query that table — should fail
        job_id2 = str(uuid.uuid4())
        path2 = str(tmp_path / f"{job_id2}.parquet")
        with pytest.raises(Exception, match="ephemeral_test"):
            execute_query(
                job_id=job_id2, sql="SELECT * FROM ephemeral_test",
                lakekeeper_url="http://localhost:8181", databases=[], result_path=path2, max_result_rows=1000,
                **s3_kwargs,
            )

    def test_setup_connection_uses_create_view_for_tables(self):
        """setup_connection() creates VIEWs (not TABLEs) for catalog objects with metadata_location."""
        import inspect
        from executor import setup_connection as sc_func

        # Read the source code to verify the SQL pattern
        source = inspect.getsource(sc_func)
        # The function should use CREATE VIEW ... iceberg_scan, not CREATE TABLE
        assert "CREATE VIEW" in source
        assert "iceberg_scan(" in source
        # It should NOT use CREATE TABLE for catalog objects
        # (CREATE TABLE is only used for ephemeral in-memory DBs via ATTACH)
        lines_with_create_table = [
            line.strip() for line in source.splitlines()
            if "CREATE TABLE" in line and "iceberg_scan" in line
        ]
        assert len(lines_with_create_table) == 0, (
            "setup_connection should not use CREATE TABLE with iceberg_scan"
        )


class TestRowCountReturnValue:
    """Return value of execute_query()."""

    @pytest.fixture(autouse=True)
    def setup(self, tmp_path):
        self.result_dir = tmp_path
        self.s3_kwargs = dict(
            s3_endpoint="http://localhost:9000",
            s3_access_key="test",
            s3_secret_key="test",
            s3_region="us-east-1",
        )

    def _run(self, sql):
        job_id = str(uuid.uuid4())
        path = str(self.result_dir / f"{job_id}.parquet")
        return execute_query(
            job_id=job_id, sql=sql, lakekeeper_url="http://localhost:8181", databases=[],
            result_path=path, max_result_rows=1000,
            **self.s3_kwargs,
        )

    def test_query_returns_correct_row_count(self):
        assert self._run("SELECT * FROM generate_series(1, 7) AS t(x)") == 7

    def test_command_returns_one(self):
        assert self._run("CREATE TABLE rc_test (id INT)") == 1

    def test_empty_query_returns_zero(self):
        assert self._run("SELECT 1 WHERE false") == 0
