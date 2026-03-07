"""Comprehensive tests for worker executor: SQL classification and query execution."""

import os
import shutil
import tempfile
import uuid

import duckdb
import pyarrow.parquet as pq
import pytest

# Add worker dir to path so we can import executor
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from executor import _classify_sql, execute_query


# ---------------------------------------------------------------------------
# _classify_sql — exhaustive classification tests
# ---------------------------------------------------------------------------

class TestClassifySqlQueries:
    """Statements that produce result sets — should be classified as 'query'."""

    # SELECT variants
    def test_simple_select(self):
        assert _classify_sql("SELECT 1") == "query"

    def test_select_from_table(self):
        assert _classify_sql("SELECT * FROM development.dbt_petkov_venelin.some_table") == "query"

    def test_select_with_limit(self):
        assert _classify_sql("SELECT id, name FROM t LIMIT 10") == "query"

    def test_select_with_where(self):
        assert _classify_sql("SELECT * FROM t WHERE id > 5") == "query"

    def test_select_with_join(self):
        assert _classify_sql("SELECT a.id, b.name FROM a JOIN b ON a.id = b.id") == "query"

    def test_select_with_subquery(self):
        assert _classify_sql("SELECT * FROM (SELECT 1 AS x) sub") == "query"

    def test_select_aggregate(self):
        assert _classify_sql("SELECT count(*), sum(amount) FROM orders GROUP BY store_id") == "query"

    def test_select_distinct(self):
        assert _classify_sql("SELECT DISTINCT category FROM products") == "query"

    def test_select_union(self):
        assert _classify_sql("SELECT 1 UNION ALL SELECT 2") == "query"

    def test_select_case_insensitive(self):
        assert _classify_sql("select 1") == "query"

    def test_select_mixed_case(self):
        assert _classify_sql("SeLeCt 1") == "query"

    def test_select_leading_whitespace(self):
        assert _classify_sql("   SELECT 1") == "query"

    def test_select_leading_newline(self):
        assert _classify_sql("\n\nSELECT 1") == "query"

    def test_select_leading_tab(self):
        assert _classify_sql("\tSELECT 1") == "query"

    # WITH (CTE)
    def test_cte_simple(self):
        assert _classify_sql("WITH cte AS (SELECT 1) SELECT * FROM cte") == "query"

    def test_cte_multiple(self):
        sql = "WITH a AS (SELECT 1), b AS (SELECT 2) SELECT * FROM a, b"
        assert _classify_sql(sql) == "query"

    def test_cte_recursive(self):
        sql = "WITH RECURSIVE cnt(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM cnt WHERE x<10) SELECT x FROM cnt"
        assert _classify_sql(sql) == "query"

    def test_cte_lowercase(self):
        assert _classify_sql("with cte as (select 1) select * from cte") == "query"

    # VALUES
    def test_values(self):
        assert _classify_sql("VALUES (1, 'a'), (2, 'b')") == "query"

    # FROM (DuckDB-specific)
    def test_from_table(self):
        assert _classify_sql("FROM my_table SELECT *") == "query"

    def test_from_table_no_select(self):
        assert _classify_sql("FROM my_table") == "query"

    def test_from_leading_whitespace(self):
        assert _classify_sql("  FROM my_table") == "query"


class TestClassifySqlCommands:
    """Statements with side-effects — should be classified as 'command'."""

    # CREATE TABLE
    def test_create_table(self):
        assert _classify_sql("CREATE TABLE t (id INT, name VARCHAR)") == "command"

    def test_create_table_as_select(self):
        assert _classify_sql("CREATE TABLE dev.schema.t AS SELECT 1 AS id, 'hello' AS name") == "command"

    def test_create_table_if_not_exists(self):
        assert _classify_sql("CREATE TABLE IF NOT EXISTS t (id INT)") == "command"

    def test_create_or_replace_table(self):
        assert _classify_sql("CREATE OR REPLACE TABLE t AS SELECT 1") == "command"

    # CREATE VIEW
    def test_create_view(self):
        assert _classify_sql("CREATE VIEW v AS SELECT 1") == "command"

    def test_create_or_replace_view(self):
        assert _classify_sql("CREATE OR REPLACE VIEW v AS SELECT 1") == "command"

    # CREATE SCHEMA
    def test_create_schema(self):
        assert _classify_sql("CREATE SCHEMA my_schema") == "command"

    # CREATE DATABASE
    def test_create_database(self):
        assert _classify_sql("CREATE DATABASE my_db") == "command"

    # INSERT
    def test_insert_values(self):
        assert _classify_sql("INSERT INTO t VALUES (1, 'hello')") == "command"

    def test_insert_select(self):
        assert _classify_sql("INSERT INTO t SELECT * FROM other") == "command"

    def test_insert_lowercase(self):
        assert _classify_sql("insert into t values (1)") == "command"

    # UPDATE
    def test_update(self):
        assert _classify_sql("UPDATE t SET name = 'world' WHERE id = 1") == "command"

    def test_update_lowercase(self):
        assert _classify_sql("update t set name = 'world'") == "command"

    # DELETE
    def test_delete(self):
        assert _classify_sql("DELETE FROM t WHERE id = 1") == "command"

    def test_delete_all(self):
        assert _classify_sql("DELETE FROM t") == "command"

    # DROP
    def test_drop_table(self):
        assert _classify_sql("DROP TABLE t") == "command"

    def test_drop_table_if_exists(self):
        assert _classify_sql("DROP TABLE IF EXISTS t") == "command"

    def test_drop_view(self):
        assert _classify_sql("DROP VIEW v") == "command"

    def test_drop_schema(self):
        assert _classify_sql("DROP SCHEMA my_schema") == "command"

    def test_drop_database(self):
        assert _classify_sql("DROP DATABASE my_db") == "command"

    # ALTER
    def test_alter_table_add_column(self):
        assert _classify_sql("ALTER TABLE t ADD COLUMN age INT") == "command"

    def test_alter_table_rename(self):
        assert _classify_sql("ALTER TABLE t RENAME TO t2") == "command"

    # TRUNCATE
    def test_truncate(self):
        assert _classify_sql("TRUNCATE TABLE t") == "command"

    # COPY
    def test_copy_to(self):
        assert _classify_sql("COPY t TO 'output.parquet' (FORMAT PARQUET)") == "command"

    def test_copy_from(self):
        assert _classify_sql("COPY t FROM 'input.csv'") == "command"

    # ATTACH / DETACH
    def test_attach(self):
        assert _classify_sql("ATTACH ':memory:' AS my_db") == "command"

    def test_detach(self):
        assert _classify_sql("DETACH my_db") == "command"

    # SET
    def test_set(self):
        assert _classify_sql("SET threads TO 4") == "command"

    # DESCRIBE / EXPLAIN / PRAGMA / SHOW — return results but can't be wrapped in CTAS
    def test_describe_table(self):
        assert _classify_sql("DESCRIBE some_table") == "command"

    def test_describe_lowercase(self):
        assert _classify_sql("describe some_table") == "command"

    def test_explain_select(self):
        assert _classify_sql("EXPLAIN SELECT * FROM t") == "command"

    def test_explain_analyze(self):
        assert _classify_sql("EXPLAIN ANALYZE SELECT * FROM t") == "command"

    def test_pragma(self):
        assert _classify_sql("PRAGMA database_list") == "command"

    def test_pragma_table_info(self):
        assert _classify_sql("PRAGMA table_info('my_table')") == "command"

    def test_show_tables(self):
        assert _classify_sql("SHOW TABLES") == "command"

    def test_show_databases(self):
        assert _classify_sql("SHOW DATABASES") == "command"

    # INSTALL / LOAD
    def test_install_extension(self):
        assert _classify_sql("INSTALL httpfs") == "command"

    def test_load_extension(self):
        assert _classify_sql("LOAD httpfs") == "command"

    # Case and whitespace for commands
    def test_create_table_lowercase(self):
        assert _classify_sql("create table t (id int)") == "command"

    def test_create_table_mixed_case(self):
        assert _classify_sql("Create Table t (id INT)") == "command"

    def test_create_table_leading_whitespace(self):
        assert _classify_sql("   CREATE TABLE t (id INT)") == "command"

    def test_create_table_leading_newline(self):
        assert _classify_sql("\nCREATE TABLE t (id INT)") == "command"


class TestClassifySqlEdgeCases:
    """Edge cases and tricky SQL patterns."""

    def test_empty_string(self):
        # Empty SQL will be classified as command (doesn't start with any query keyword)
        assert _classify_sql("") == "command"

    def test_whitespace_only(self):
        assert _classify_sql("   ") == "command"

    def test_select_into(self):
        # SELECT INTO is technically a query keyword prefix but creates a table
        # Our classifier treats it as query (acceptable — DuckDB doesn't support SELECT INTO)
        assert _classify_sql("SELECT * INTO new_table FROM old_table") == "query"

    def test_cte_with_insert(self):
        # WITH ... INSERT is a command but starts with WITH
        # Known limitation: classified as query
        sql = "WITH data AS (SELECT 1 AS id) INSERT INTO t SELECT * FROM data"
        assert _classify_sql(sql) == "query"  # known limitation

    def test_multiline_select(self):
        sql = """
        SELECT
            id,
            name
        FROM my_table
        WHERE id > 0
        """
        assert _classify_sql(sql) == "query"

    def test_multiline_create(self):
        sql = """
        CREATE TABLE test_table AS
        SELECT 1 AS id, 'hello' AS name
        """
        assert _classify_sql(sql) == "command"


# ---------------------------------------------------------------------------
# execute_query — integration tests with real DuckDB
# ---------------------------------------------------------------------------

class TestExecuteQueryIntegration:
    """Integration tests for execute_query with a real DuckDB connection.

    Uses local temp files for result_path (no S3 needed).
    Uses empty catalog_objects (no Iceberg needed).
    """

    @pytest.fixture(autouse=True)
    def setup_result_dir(self, tmp_path):
        self.result_dir = tmp_path
        self.s3_endpoint = "http://localhost:9000"
        self.s3_access_key = "test"
        self.s3_secret_key = "test"
        self.s3_region = "us-east-1"

    def _result_path(self):
        return str(self.result_dir / f"{uuid.uuid4()}.parquet")

    def _run(self, sql, max_rows=1000):
        job_id = str(uuid.uuid4())
        path = self._result_path()
        row_count = execute_query(
            job_id=job_id,
            sql=sql,
            pg_connection_string="", databases=[],
            s3_endpoint=self.s3_endpoint,
            s3_access_key=self.s3_access_key,
            s3_secret_key=self.s3_secret_key,
            s3_region=self.s3_region,
            result_path=path,
            max_result_rows=max_rows,
        )
        table = pq.read_table(path)
        return row_count, table

    # --- Query path (CTAS wrapper) ---

    def test_select_literal(self):
        row_count, table = self._run("SELECT 1 AS x")
        assert row_count == 1
        assert table.num_rows == 1
        assert table.column_names == ["x"]
        assert table.column("x").to_pylist() == [1]

    def test_select_multiple_rows(self):
        row_count, table = self._run("SELECT * FROM generate_series(1, 5) AS t(x)")
        assert row_count == 5
        assert table.num_rows == 5

    def test_select_with_explicit_limit(self):
        row_count, table = self._run("SELECT * FROM generate_series(1, 100) AS t(x) LIMIT 3")
        assert row_count == 3
        assert table.num_rows == 3

    def test_select_auto_limit(self):
        """Without explicit LIMIT, executor adds LIMIT max_result_rows."""
        row_count, table = self._run(
            "SELECT * FROM generate_series(1, 100) AS t(x)",
            max_rows=10,
        )
        assert row_count == 10
        assert table.num_rows == 10

    def test_select_with_semicolon(self):
        row_count, table = self._run("SELECT 42 AS answer;")
        assert row_count == 1
        assert table.column("answer").to_pylist() == [42]

    def test_select_with_trailing_whitespace_and_semicolon(self):
        row_count, table = self._run("SELECT 42 AS answer ;  ")
        assert row_count == 1

    def test_cte_query(self):
        sql = "WITH nums AS (SELECT unnest([1,2,3]) AS n) SELECT n * 2 AS doubled FROM nums"
        row_count, table = self._run(sql)
        assert row_count == 3
        assert sorted(table.column("doubled").to_pylist()) == [2, 4, 6]

    def test_values_query(self):
        row_count, table = self._run("VALUES (1, 'a'), (2, 'b'), (3, 'c')")
        assert row_count == 3
        assert table.num_rows == 3

    def test_explain_query(self):
        """EXPLAIN can't be wrapped in CTAS — goes through command path."""
        row_count, table = self._run("EXPLAIN SELECT 1")
        assert row_count == 1
        assert table.column("status").to_pylist() == ["OK"]

    def test_describe_query(self):
        """DESCRIBE can't be wrapped in CTAS — goes through command path."""
        row_count, table = self._run(
            "DESCRIBE SELECT 1 AS id, 'hello' AS name"
        )
        assert row_count == 1
        assert table.column("status").to_pylist() == ["OK"]

    def test_pragma_query(self):
        """PRAGMA can't be wrapped in CTAS — goes through command path."""
        row_count, table = self._run("PRAGMA database_list")
        assert row_count == 1
        assert table.column("status").to_pylist() == ["OK"]

    def test_from_syntax(self):
        """DuckDB FROM-first syntax."""
        row_count, table = self._run("FROM (SELECT 1 AS x, 2 AS y)")
        assert row_count == 1
        assert table.column_names == ["x", "y"]

    def test_select_string_columns(self):
        row_count, table = self._run("SELECT 'hello' AS greeting, 'world' AS target")
        assert row_count == 1
        assert table.column("greeting").to_pylist() == ["hello"]

    def test_select_null(self):
        row_count, table = self._run("SELECT NULL AS empty")
        assert row_count == 1

    def test_select_multiple_types(self):
        sql = "SELECT 1 AS int_col, 1.5 AS float_col, 'text' AS str_col, true AS bool_col"
        row_count, table = self._run(sql)
        assert row_count == 1
        assert table.column("int_col").to_pylist() == [1]
        assert table.column("bool_col").to_pylist() == [True]

    # --- Command path (direct execution) ---

    def test_create_table_as_select(self):
        """CTAS should go through command path, not be wrapped in another CTAS."""
        row_count, table = self._run(
            "CREATE TABLE ctas_test AS SELECT 1 AS id, 'hello' AS name"
        )
        assert row_count == 1
        assert "status" in table.column_names
        assert "rows_affected" in table.column_names
        assert table.column("status").to_pylist() == ["OK"]

    def test_create_table_ddl(self):
        row_count, table = self._run("CREATE TABLE ddl_test (id INTEGER, name VARCHAR)")
        assert row_count == 1
        assert table.column("status").to_pylist() == ["OK"]

    def test_create_and_insert(self):
        """Create a table then insert — two separate execute_query calls."""
        # Create
        row_count, table = self._run("CREATE TABLE ins_test (id INT, val TEXT)")
        assert table.column("status").to_pylist() == ["OK"]

        # Insert (separate connection, won't see the table — expect error)
        # Each execute_query creates a fresh connection, so this will fail
        # This is expected: each job is independent
        with pytest.raises(Exception):
            self._run("INSERT INTO ins_test VALUES (1, 'a')")

    def test_create_table_if_not_exists(self):
        row_count, table = self._run("CREATE TABLE IF NOT EXISTS test_cine (x INT)")
        assert row_count == 1
        assert table.column("status").to_pylist() == ["OK"]

    def test_create_or_replace_table(self):
        row_count, table = self._run("CREATE OR REPLACE TABLE test_cor AS SELECT 1 AS x")
        assert row_count == 1
        assert table.column("status").to_pylist() == ["OK"]

    def test_create_view(self):
        row_count, table = self._run("CREATE VIEW test_view AS SELECT 42 AS answer")
        assert row_count == 1
        assert table.column("status").to_pylist() == ["OK"]

    def test_drop_table_if_exists(self):
        row_count, table = self._run("DROP TABLE IF EXISTS nonexistent_table")
        assert row_count == 1
        assert table.column("status").to_pylist() == ["OK"]

    def test_drop_view_if_exists(self):
        row_count, table = self._run("DROP VIEW IF EXISTS nonexistent_view")
        assert row_count == 1
        assert table.column("status").to_pylist() == ["OK"]

    def test_set_command(self):
        row_count, table = self._run("SET threads TO 2")
        assert row_count == 1
        assert table.column("status").to_pylist() == ["OK"]

    def test_alter_table(self):
        """ALTER on a freshly created in-memory table (same connection won't work
        since each call is independent, but ALTER on a nonexistent table should error)."""
        with pytest.raises(Exception):
            self._run("ALTER TABLE nonexistent ADD COLUMN x INT")

    def test_command_with_semicolon(self):
        row_count, table = self._run("CREATE TABLE semi_test (x INT);")
        assert row_count == 1
        assert table.column("status").to_pylist() == ["OK"]

    def test_command_with_leading_whitespace(self):
        row_count, table = self._run("  \n  CREATE TABLE ws_test (x INT)  \n  ")
        assert row_count == 1
        assert table.column("status").to_pylist() == ["OK"]

    def test_command_lowercase(self):
        row_count, table = self._run("create table lc_test as select 1 as id")
        assert row_count == 1
        assert table.column("status").to_pylist() == ["OK"]

    def test_rows_affected_for_ctas(self):
        """CTAS returns row count in DuckDB."""
        row_count, table = self._run(
            "CREATE TABLE ra_test AS SELECT * FROM generate_series(1, 5) AS t(x)"
        )
        assert row_count == 1
        affected = table.column("rows_affected").to_pylist()[0]
        assert affected == 5

    # --- Error handling ---

    def test_invalid_sql_raises(self):
        with pytest.raises(Exception):
            self._run("THIS IS NOT VALID SQL")

    def test_select_from_nonexistent_table(self):
        with pytest.raises(Exception):
            self._run("SELECT * FROM nonexistent_table_xyz")

    def test_create_table_invalid_syntax(self):
        with pytest.raises(Exception):
            self._run("CREATE TABLE")

    # --- Cancellation support ---

    def test_job_id_cleanup(self):
        """After execute_query completes, the job_id should be removed from _running_conns."""
        from executor import _running_conns

        job_id = str(uuid.uuid4())
        path = self._result_path()
        execute_query(
            job_id=job_id,
            sql="SELECT 1",
            pg_connection_string="", databases=[],
            s3_endpoint=self.s3_endpoint,
            s3_access_key=self.s3_access_key,
            s3_secret_key=self.s3_secret_key,
            s3_region=self.s3_region,
            result_path=path,
            max_result_rows=1000,
        )
        assert job_id not in _running_conns

    def test_job_id_cleanup_on_error(self):
        """Even on error, job_id should be cleaned up."""
        from executor import _running_conns

        job_id = str(uuid.uuid4())
        path = self._result_path()
        with pytest.raises(Exception):
            execute_query(
                job_id=job_id,
                sql="SELECT * FROM nonexistent_xyz",
                pg_connection_string="", databases=[],
                s3_endpoint=self.s3_endpoint,
                s3_access_key=self.s3_access_key,
                s3_secret_key=self.s3_secret_key,
                s3_region=self.s3_region,
                result_path=path,
                max_result_rows=1000,
            )
        assert job_id not in _running_conns
