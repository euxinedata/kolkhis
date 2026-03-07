"""E2E tests for queries through the SQL editor (job-based worker path).

Run: cd backend && uv run pytest tests/test_queries_e2e.py -v
"""

from conftest import SCHEMA, safe_cleanup, submit_and_wait


class TestSimpleQueries:
    """Basic SELECT queries against DuckLake tables."""

    def test_simple_select(self, api):
        status, job, results = submit_and_wait(
            api, "SELECT brand_id, name FROM retail_catalog.products.brands LIMIT 5"
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert len(results["rows"]) == 5
        assert "brand_id" in results["columns"]
        assert "name" in results["columns"]

    def test_select_count(self, api):
        status, job, results = submit_and_wait(
            api, "SELECT count(*) AS cnt FROM retail_catalog.products.brands"
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert results["rows"][0]["cnt"] > 0

    def test_select_with_where(self, api):
        status, job, results = submit_and_wait(
            api, "SELECT * FROM retail_catalog.products.brands WHERE brand_id = 1"
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert len(results["rows"]) == 1

    def test_user_limit_respected(self, api):
        status, job, results = submit_and_wait(
            api, "SELECT * FROM retail_catalog.products.brands LIMIT 2"
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert len(results["rows"]) == 2


class TestComplexQueries:
    """More complex SQL patterns."""

    def test_group_by(self, api):
        status, job, results = submit_and_wait(
            api,
            "SELECT country_of_origin, count(*) AS cnt "
            "FROM retail_catalog.products.brands GROUP BY 1 LIMIT 5",
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert "country_of_origin" in results["columns"]
        assert "cnt" in results["columns"]

    def test_cte_query(self, api):
        status, job, results = submit_and_wait(
            api,
            "WITH b AS (SELECT * FROM retail_catalog.products.brands LIMIT 5) "
            "SELECT count(*) AS cnt FROM b",
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert results["rows"][0]["cnt"] == 5

    def test_same_db_join(self, api):
        status, job, results = submit_and_wait(
            api,
            "SELECT p.name, b.name AS brand "
            "FROM retail_catalog.products.products p "
            "JOIN retail_catalog.products.brands b ON p.brand_id = b.brand_id "
            "LIMIT 5",
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert "name" in results["columns"]
        assert "brand" in results["columns"]

    def test_cross_database_join(self, api):
        status, job, results = submit_and_wait(
            api,
            "SELECT c.first_name, l.tier "
            "FROM retail_sales.customers.customers c "
            "JOIN retail_sales.customers.loyalty_accounts l ON c.customer_id = l.customer_id "
            "LIMIT 5",
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert len(results["rows"]) > 0


class TestResultPagination:
    """Result response structure."""

    def test_result_pagination_fields(self, api):
        status, job, results = submit_and_wait(
            api, "SELECT * FROM retail_catalog.products.brands LIMIT 3"
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert "columns" in results
        assert "rows" in results
        assert "total" in results
        assert "page" in results
        assert "page_size" in results


class TestWindowFunctions:
    """Window functions against DuckLake tables."""

    def test_row_number(self, api):
        status, job, results = submit_and_wait(
            api,
            "SELECT brand_id, name, "
            "ROW_NUMBER() OVER (ORDER BY brand_id) AS rn "
            "FROM retail_catalog.products.brands LIMIT 5",
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert "rn" in results["columns"]
        # ROW_NUMBER should produce sequential integers
        rns = [r["rn"] for r in results["rows"]]
        assert rns == list(range(1, 6))

    def test_rank_and_dense_rank(self, api):
        status, job, results = submit_and_wait(
            api,
            "SELECT brand_id, country_of_origin, "
            "RANK() OVER (ORDER BY country_of_origin) AS rnk, "
            "DENSE_RANK() OVER (ORDER BY country_of_origin) AS drnk "
            "FROM retail_catalog.products.brands LIMIT 10",
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert "rnk" in results["columns"]
        assert "drnk" in results["columns"]
        # DENSE_RANK should be <= RANK for every row
        for r in results["rows"]:
            assert r["drnk"] <= r["rnk"]

    def test_lag_and_lead(self, api):
        status, job, results = submit_and_wait(
            api,
            "SELECT brand_id, name, "
            "LAG(name) OVER (ORDER BY brand_id) AS prev_name, "
            "LEAD(name) OVER (ORDER BY brand_id) AS next_name "
            "FROM retail_catalog.products.brands "
            "ORDER BY brand_id LIMIT 3",
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        # First row's LAG should be NULL (no preceding row)
        assert results["rows"][0]["prev_name"] is None
        # Second row's LAG should be the first row's name
        assert results["rows"][1]["prev_name"] == results["rows"][0]["name"]
        # First row's LEAD should be the second row's name
        assert results["rows"][0]["next_name"] == results["rows"][1]["name"]

    def test_running_sum(self, api):
        status, job, results = submit_and_wait(
            api,
            "SELECT brand_id, "
            "SUM(brand_id) OVER (ORDER BY brand_id "
            "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS running_sum "
            "FROM retail_catalog.products.brands "
            "ORDER BY brand_id LIMIT 4",
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        # Running sum: 1, 1+2=3, 1+2+3=6, 1+2+3+4=10
        sums = [r["running_sum"] for r in results["rows"]]
        assert sums == [1, 3, 6, 10]

    def test_partition_by(self, api):
        status, job, results = submit_and_wait(
            api,
            "SELECT brand_id, country_of_origin, "
            "ROW_NUMBER() OVER (PARTITION BY country_of_origin ORDER BY brand_id) AS rn "
            "FROM retail_catalog.products.brands LIMIT 10",
        )
        assert status == "completed", f"Expected completed, got {job.get('error')}"
        assert "rn" in results["columns"]
        # All rn values should be >= 1
        assert all(r["rn"] >= 1 for r in results["rows"])


class TestCreateTableViaEditor:
    """CREATE TABLE via SQL editor must land in DuckLake (visible in catalog)."""

    def test_create_table_appears_in_catalog(self, api):
        """CREATE TABLE AS SELECT via SQL editor should persist in DuckLake."""
        try:
            status, job, _ = submit_and_wait(
                api,
                f"CREATE TABLE development.{SCHEMA}.e2e_editor_tbl "
                f"AS SELECT 1 AS id, 'test' AS name",
            )
            assert status == "completed", f"Expected completed, got {job.get('error')}"

            # Table must be visible in the catalog, not ephemeral
            resp = api.get(
                f"/api/catalog/databases/development/schemas/{SCHEMA}/objects"
            )
            resp.raise_for_status()
            objects = resp.json()["objects"]
            names = [o["name"] for o in objects]
            assert "e2e_editor_tbl" in names, (
                f"Table not in catalog. Objects: {names}"
            )
        finally:
            safe_cleanup(
                api, f"DROP TABLE development.{SCHEMA}.e2e_editor_tbl"
            )


class TestDmlPersistenceViaEditor:
    """DML via SQL editor (job-based path) must persist across connections.

    Each SQL editor query gets its own ephemeral DuckDB connection that
    ATTACHes DuckLake, executes, then disconnects. DML must persist because
    DuckLake writes to PostgreSQL metadata + S3 Parquet — but this needs
    verification since each job is a separate connection.
    """

    def test_insert_persists_across_jobs(self, api):
        """INSERT via SQL editor persists and is readable in a subsequent job."""
        try:
            # Job 1: Create table
            status, job, _ = submit_and_wait(
                api,
                f"CREATE TABLE development.{SCHEMA}.e2e_editor_insert "
                f"AS SELECT 1 AS id, 'first' AS val",
            )
            assert status == "completed", f"CREATE failed: {job.get('error')}"

            # Job 2: INSERT (separate connection)
            status, job, _ = submit_and_wait(
                api,
                f"INSERT INTO development.{SCHEMA}.e2e_editor_insert "
                f"VALUES (2, 'second')",
            )
            assert status == "completed", f"INSERT failed: {job.get('error')}"

            # Job 3: Verify (yet another connection)
            status, job, results = submit_and_wait(
                api,
                f"SELECT * FROM development.{SCHEMA}.e2e_editor_insert ORDER BY id",
            )
            assert status == "completed", f"SELECT failed: {job.get('error')}"
            assert len(results["rows"]) == 2, (
                f"Expected 2 rows after INSERT, got {len(results['rows'])}"
            )
            assert results["rows"][0]["id"] == 1
            assert results["rows"][1]["id"] == 2
        finally:
            safe_cleanup(api, f"DROP TABLE development.{SCHEMA}.e2e_editor_insert")

    def test_update_persists_across_jobs(self, api):
        """UPDATE via SQL editor persists and is readable in a subsequent job."""
        try:
            # Job 1: Create table
            status, job, _ = submit_and_wait(
                api,
                f"CREATE TABLE development.{SCHEMA}.e2e_editor_update "
                f"AS SELECT * FROM (VALUES (1, 'old'), (2, 'keep')) AS t(id, val)",
            )
            assert status == "completed", f"CREATE failed: {job.get('error')}"

            # Job 2: UPDATE (separate connection)
            status, job, _ = submit_and_wait(
                api,
                f"UPDATE development.{SCHEMA}.e2e_editor_update "
                f"SET val = 'new' WHERE id = 1",
            )
            assert status == "completed", f"UPDATE failed: {job.get('error')}"

            # Job 3: Verify
            status, job, results = submit_and_wait(
                api,
                f"SELECT * FROM development.{SCHEMA}.e2e_editor_update ORDER BY id",
            )
            assert status == "completed", f"SELECT failed: {job.get('error')}"
            assert len(results["rows"]) == 2
            rows_by_id = {r["id"]: r["val"] for r in results["rows"]}
            assert rows_by_id[1] == "new", f"Row 1 not updated: {rows_by_id}"
            assert rows_by_id[2] == "keep", f"Row 2 changed unexpectedly: {rows_by_id}"
        finally:
            safe_cleanup(api, f"DROP TABLE development.{SCHEMA}.e2e_editor_update")

    def test_delete_persists_across_jobs(self, api):
        """DELETE via SQL editor persists and is readable in a subsequent job."""
        try:
            # Job 1: Create table with 3 rows
            status, job, _ = submit_and_wait(
                api,
                f"CREATE TABLE development.{SCHEMA}.e2e_editor_delete "
                f"AS SELECT * FROM (VALUES (1, 'a'), (2, 'b'), (3, 'c')) AS t(id, val)",
            )
            assert status == "completed", f"CREATE failed: {job.get('error')}"

            # Job 2: DELETE (separate connection)
            status, job, _ = submit_and_wait(
                api,
                f"DELETE FROM development.{SCHEMA}.e2e_editor_delete WHERE id = 2",
            )
            assert status == "completed", f"DELETE failed: {job.get('error')}"

            # Job 3: Verify
            status, job, results = submit_and_wait(
                api,
                f"SELECT * FROM development.{SCHEMA}.e2e_editor_delete ORDER BY id",
            )
            assert status == "completed", f"SELECT failed: {job.get('error')}"
            assert len(results["rows"]) == 2, (
                f"Expected 2 rows after DELETE, got {len(results['rows'])}"
            )
            ids = [r["id"] for r in results["rows"]]
            assert ids == [1, 3], f"Wrong rows after DELETE: {ids}"
        finally:
            safe_cleanup(api, f"DROP TABLE development.{SCHEMA}.e2e_editor_delete")

    def test_merge_persists_across_jobs(self, api):
        """MERGE via SQL editor persists and is readable in a subsequent job."""
        try:
            # Job 1: Create table
            status, job, _ = submit_and_wait(
                api,
                f"CREATE TABLE development.{SCHEMA}.e2e_editor_merge "
                f"AS SELECT * FROM (VALUES (1, 'a'), (2, 'b')) AS t(id, val)",
            )
            assert status == "completed", f"CREATE failed: {job.get('error')}"

            # Job 2: MERGE (separate connection)
            status, job, _ = submit_and_wait(
                api,
                f"MERGE INTO development.{SCHEMA}.e2e_editor_merge AS target "
                f"USING (SELECT * FROM (VALUES (2, 'updated'), (3, 'new')) AS t(id, val)) AS source "
                f"ON target.id = source.id "
                f"WHEN MATCHED THEN UPDATE SET val = source.val "
                f"WHEN NOT MATCHED THEN INSERT (id, val) VALUES (source.id, source.val)",
            )
            assert status == "completed", f"MERGE failed: {job.get('error')}"

            # Job 3: Verify
            status, job, results = submit_and_wait(
                api,
                f"SELECT * FROM development.{SCHEMA}.e2e_editor_merge ORDER BY id",
            )
            assert status == "completed", f"SELECT failed: {job.get('error')}"
            assert len(results["rows"]) == 3
            rows_by_id = {r["id"]: r["val"] for r in results["rows"]}
            assert rows_by_id[1] == "a"
            assert rows_by_id[2] == "updated"
            assert rows_by_id[3] == "new"
        finally:
            safe_cleanup(api, f"DROP TABLE development.{SCHEMA}.e2e_editor_merge")

    def test_multiple_inserts_accumulate(self, api):
        """Multiple INSERT jobs accumulate rows correctly."""
        try:
            submit_and_wait(
                api,
                f"CREATE TABLE development.{SCHEMA}.e2e_editor_multi "
                f"AS SELECT 1 AS id",
            )

            for i in range(2, 5):
                status, job, _ = submit_and_wait(
                    api,
                    f"INSERT INTO development.{SCHEMA}.e2e_editor_multi VALUES ({i})",
                )
                assert status == "completed", f"INSERT {i} failed: {job.get('error')}"

            status, job, results = submit_and_wait(
                api,
                f"SELECT * FROM development.{SCHEMA}.e2e_editor_multi ORDER BY id",
            )
            assert status == "completed"
            ids = [r["id"] for r in results["rows"]]
            assert ids == [1, 2, 3, 4], f"Expected [1,2,3,4], got {ids}"
        finally:
            safe_cleanup(api, f"DROP TABLE development.{SCHEMA}.e2e_editor_multi")


class TestAlterTableViaEditor:
    """ALTER TABLE operations via SQL editor — schema evolution in DuckLake."""

    def test_add_column(self, api):
        """ALTER TABLE ADD COLUMN adds a column visible in catalog and queries."""
        try:
            submit_and_wait(
                api,
                f"CREATE TABLE development.{SCHEMA}.e2e_alter_tbl "
                f"AS SELECT 1 AS id, 'hello' AS name",
            )

            status, job, _ = submit_and_wait(
                api,
                f"ALTER TABLE development.{SCHEMA}.e2e_alter_tbl ADD COLUMN score INTEGER",
            )
            assert status == "completed", f"ADD COLUMN failed: {job.get('error')}"

            # Verify column in catalog
            resp = api.get(
                f"/api/catalog/databases/development/schemas/{SCHEMA}/objects/e2e_alter_tbl/schema"
            )
            resp.raise_for_status()
            col_names = [c["name"] for c in resp.json()["columns"]]
            assert "score" in col_names, f"New column not in catalog: {col_names}"

            # Verify queryable — new column should be NULL for existing rows
            status, job, results = submit_and_wait(
                api, f"SELECT * FROM development.{SCHEMA}.e2e_alter_tbl"
            )
            assert status == "completed"
            assert results["rows"][0]["score"] is None
            assert results["rows"][0]["name"] == "hello"
        finally:
            safe_cleanup(api, f"DROP TABLE development.{SCHEMA}.e2e_alter_tbl")

    def test_drop_column(self, api):
        """ALTER TABLE DROP COLUMN removes a column from catalog and queries."""
        try:
            submit_and_wait(
                api,
                f"CREATE TABLE development.{SCHEMA}.e2e_alter_tbl "
                f"AS SELECT 1 AS id, 'hello' AS name, 42 AS val",
            )

            status, job, _ = submit_and_wait(
                api,
                f"ALTER TABLE development.{SCHEMA}.e2e_alter_tbl DROP COLUMN val",
            )
            assert status == "completed", f"DROP COLUMN failed: {job.get('error')}"

            # Verify column gone from catalog
            resp = api.get(
                f"/api/catalog/databases/development/schemas/{SCHEMA}/objects/e2e_alter_tbl/schema"
            )
            resp.raise_for_status()
            col_names = [c["name"] for c in resp.json()["columns"]]
            assert "val" not in col_names, f"Dropped column still in catalog: {col_names}"
            assert col_names == ["id", "name"]

            # Verify data intact for remaining columns
            status, job, results = submit_and_wait(
                api, f"SELECT * FROM development.{SCHEMA}.e2e_alter_tbl"
            )
            assert status == "completed"
            assert "val" not in results["columns"]
            assert results["rows"][0]["id"] == 1
            assert results["rows"][0]["name"] == "hello"
        finally:
            safe_cleanup(api, f"DROP TABLE development.{SCHEMA}.e2e_alter_tbl")

    def test_rename_column(self, api):
        """ALTER TABLE RENAME COLUMN changes column name in catalog and queries."""
        try:
            submit_and_wait(
                api,
                f"CREATE TABLE development.{SCHEMA}.e2e_alter_tbl "
                f"AS SELECT 1 AS id, 'hello' AS old_name",
            )

            status, job, _ = submit_and_wait(
                api,
                f"ALTER TABLE development.{SCHEMA}.e2e_alter_tbl "
                f"RENAME COLUMN old_name TO new_name",
            )
            assert status == "completed", f"RENAME COLUMN failed: {job.get('error')}"

            # Verify in catalog
            resp = api.get(
                f"/api/catalog/databases/development/schemas/{SCHEMA}/objects/e2e_alter_tbl/schema"
            )
            resp.raise_for_status()
            col_names = [c["name"] for c in resp.json()["columns"]]
            assert "new_name" in col_names, f"Renamed column not in catalog: {col_names}"
            assert "old_name" not in col_names

            # Verify queryable under new name
            status, job, results = submit_and_wait(
                api, f"SELECT new_name FROM development.{SCHEMA}.e2e_alter_tbl"
            )
            assert status == "completed"
            assert results["rows"][0]["new_name"] == "hello"
        finally:
            safe_cleanup(api, f"DROP TABLE development.{SCHEMA}.e2e_alter_tbl")

    def test_alter_column_type(self, api):
        """ALTER TABLE ALTER COLUMN TYPE changes column type."""
        try:
            submit_and_wait(
                api,
                f"CREATE TABLE development.{SCHEMA}.e2e_alter_tbl "
                f"AS SELECT 1 AS id, 42 AS val",
            )

            status, job, _ = submit_and_wait(
                api,
                f"ALTER TABLE development.{SCHEMA}.e2e_alter_tbl "
                f"ALTER COLUMN val TYPE BIGINT",
            )
            assert status == "completed", f"ALTER TYPE failed: {job.get('error')}"

            # Verify type changed in catalog
            resp = api.get(
                f"/api/catalog/databases/development/schemas/{SCHEMA}/objects/e2e_alter_tbl/schema"
            )
            resp.raise_for_status()
            cols = {c["name"]: c["type"] for c in resp.json()["columns"]}
            assert cols["val"] == "int64", f"Type not changed: {cols}"

            # Verify data intact
            status, job, results = submit_and_wait(
                api, f"SELECT val FROM development.{SCHEMA}.e2e_alter_tbl"
            )
            assert status == "completed"
            assert results["rows"][0]["val"] == 42
        finally:
            safe_cleanup(api, f"DROP TABLE development.{SCHEMA}.e2e_alter_tbl")

    def test_add_column_then_insert(self, api):
        """ADD COLUMN followed by INSERT with new column works end-to-end."""
        try:
            submit_and_wait(
                api,
                f"CREATE TABLE development.{SCHEMA}.e2e_alter_tbl "
                f"AS SELECT 1 AS id",
            )

            submit_and_wait(
                api,
                f"ALTER TABLE development.{SCHEMA}.e2e_alter_tbl ADD COLUMN tag VARCHAR",
            )

            status, job, _ = submit_and_wait(
                api,
                f"INSERT INTO development.{SCHEMA}.e2e_alter_tbl VALUES (2, 'new_row')",
            )
            assert status == "completed", f"INSERT failed: {job.get('error')}"

            status, job, results = submit_and_wait(
                api,
                f"SELECT * FROM development.{SCHEMA}.e2e_alter_tbl ORDER BY id",
            )
            assert status == "completed"
            assert len(results["rows"]) == 2
            # Row 1: tag should be NULL (existed before ADD COLUMN)
            assert results["rows"][0]["tag"] is None
            # Row 2: tag should be 'new_row'
            assert results["rows"][1]["tag"] == "new_row"
        finally:
            safe_cleanup(api, f"DROP TABLE development.{SCHEMA}.e2e_alter_tbl")
