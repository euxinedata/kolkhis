"""End-to-end tests for the view lifecycle as experienced by users.

Simulates the actual SQL editor and dbt workflows:
1. CREATE VIEW → stored → later query resolves through overlay
2. Views referencing real tables work through the overlay
3. DROP VIEW removes the view, subsequent queries fail

No external services needed: uses :memory: DuckDB databases to simulate
Iceberg, and exercises the real execute_query() code path.
"""

import os
import sys

import duckdb
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from executor import _setup_overlay


# ---------------------------------------------------------------------------
# SQL Editor Flow: CREATE VIEW → query view
# ---------------------------------------------------------------------------

class TestSqlEditorViewFlow:
    """Simulates what happens when a user creates and queries views in the SQL editor.

    The SQL editor flow:
    1. User sends CREATE VIEW → backend detect_ddl() intercepts, stores in PG
    2. User sends SELECT from view → backend loads views from PG, sends to worker
    3. Worker sets up overlay with views, executes query
    """

    @pytest.fixture
    def conn(self):
        c = duckdb.connect()
        yield c
        c.close()

    def test_create_view_then_query_it(self, conn):
        """User creates a view, then queries it — the core happy path."""
        # Step 1: Simulate Iceberg database with a table
        ice_name = "_ice_development"
        conn.execute(f"""ATTACH ':memory:' AS "{ice_name}" """)
        conn.execute(f'CREATE SCHEMA "{ice_name}"."public"')
        conn.execute(
            f'CREATE TABLE "{ice_name}"."public"."customers" AS '
            f"SELECT 1 AS id, 'Alice' AS name, 100 AS balance "
            f"UNION ALL SELECT 2, 'Bob', 200"
        )

        # Step 2: Simulate what the backend stores after CREATE VIEW
        # This is the format loaded from org_views and sent to worker
        views = [
            {
                "database": "development",
                "schema_name": "public",
                "name": "rich_customers",
                "view_sql": (
                    'SELECT * FROM "development"."public"."customers" '
                    "WHERE balance > 150"
                ),
            }
        ]

        # Step 3: Worker sets up overlay (this is what execute_query does internally)
        _setup_overlay(conn, "development", ice_name, views=views)

        # Step 4: User queries the view
        result = conn.execute(
            'SELECT name FROM "development"."public"."rich_customers"'
        ).fetchall()
        assert result == [("Bob",)]

    def test_create_view_with_aggregation(self, conn):
        """View with GROUP BY and aggregation."""
        ice_name = "_ice_development"
        conn.execute(f"""ATTACH ':memory:' AS "{ice_name}" """)
        conn.execute(f'CREATE SCHEMA "{ice_name}"."sales"')
        conn.execute(
            f'CREATE TABLE "{ice_name}"."sales"."orders" AS '
            f"SELECT 1 AS customer_id, 50.0 AS amount "
            f"UNION ALL SELECT 1, 75.0 "
            f"UNION ALL SELECT 2, 100.0"
        )

        views = [
            {
                "database": "development",
                "schema_name": "sales",
                "name": "customer_totals",
                "view_sql": (
                    "SELECT customer_id, sum(amount) AS total "
                    'FROM "development"."sales"."orders" '
                    "GROUP BY customer_id"
                ),
            }
        ]

        _setup_overlay(conn, "development", ice_name, views=views)

        result = conn.execute(
            'SELECT customer_id, total FROM "development"."sales"."customer_totals" '
            "ORDER BY customer_id"
        ).fetchall()
        assert result == [(1, 125.0), (2, 100.0)]

    def test_create_or_replace_view_updates_definition(self, conn):
        """CREATE OR REPLACE VIEW changes what the view returns."""
        ice_name = "_ice_development"
        conn.execute(f"""ATTACH ':memory:' AS "{ice_name}" """)
        conn.execute(f'CREATE SCHEMA "{ice_name}"."public"')
        conn.execute(
            f'CREATE TABLE "{ice_name}"."public"."data" AS '
            f"SELECT 1 AS x UNION ALL SELECT 2 UNION ALL SELECT 3"
        )

        # First version of the view
        views_v1 = [
            {
                "database": "development",
                "schema_name": "public",
                "name": "filtered",
                "view_sql": 'SELECT * FROM "development"."public"."data" WHERE x > 1',
            }
        ]
        _setup_overlay(conn, "development", ice_name, views=views_v1)
        result = conn.execute('SELECT x FROM "development"."public"."filtered" ORDER BY x').fetchall()
        assert result == [(2,), (3,)]

        # Simulate DROP + re-overlay (what happens on next query after OR REPLACE)
        conn.execute('DETACH "development"')
        views_v2 = [
            {
                "database": "development",
                "schema_name": "public",
                "name": "filtered",
                "view_sql": 'SELECT * FROM "development"."public"."data" WHERE x > 2',
            }
        ]
        _setup_overlay(conn, "development", ice_name, views=views_v2)
        result = conn.execute('SELECT x FROM "development"."public"."filtered" ORDER BY x').fetchall()
        assert result == [(3,)]

    def test_view_alongside_regular_tables(self, conn):
        """Views and tables coexist — user can query both."""
        ice_name = "_ice_development"
        conn.execute(f"""ATTACH ':memory:' AS "{ice_name}" """)
        conn.execute(f'CREATE SCHEMA "{ice_name}"."public"')
        conn.execute(
            f'CREATE TABLE "{ice_name}"."public"."users" AS '
            f"SELECT 1 AS id, 'Alice' AS name"
        )
        conn.execute(
            f'CREATE TABLE "{ice_name}"."public"."orders" AS '
            f"SELECT 100 AS order_id, 1 AS user_id"
        )

        views = [
            {
                "database": "development",
                "schema_name": "public",
                "name": "user_orders",
                "view_sql": (
                    'SELECT u.name, o.order_id FROM "development"."public"."users" u '
                    'JOIN "development"."public"."orders" o ON u.id = o.user_id'
                ),
            }
        ]
        _setup_overlay(conn, "development", ice_name, views=views)

        # Query a table directly
        users = conn.execute('SELECT name FROM "development"."public"."users"').fetchall()
        assert users == [("Alice",)]

        # Query the view
        result = conn.execute(
            'SELECT name, order_id FROM "development"."public"."user_orders"'
        ).fetchall()
        assert result == [("Alice", 100)]

    def test_cross_schema_view(self, conn):
        """View in one schema references a table in another schema."""
        ice_name = "_ice_development"
        conn.execute(f"""ATTACH ':memory:' AS "{ice_name}" """)
        conn.execute(f'CREATE SCHEMA "{ice_name}"."raw"')
        conn.execute(
            f'CREATE TABLE "{ice_name}"."raw"."events" AS '
            f"SELECT 'click' AS event_type, 10 AS count"
        )

        views = [
            {
                "database": "development",
                "schema_name": "analytics",
                "name": "event_summary",
                "view_sql": 'SELECT * FROM "development"."raw"."events"',
            }
        ]
        _setup_overlay(conn, "development", ice_name, views=views)

        result = conn.execute(
            'SELECT event_type, count FROM "development"."analytics"."event_summary"'
        ).fetchall()
        assert result == [("click", 10)]

    def test_drop_view_makes_it_unqueryable(self, conn):
        """After DROP VIEW (removing from views list), the view no longer resolves."""
        ice_name = "_ice_development"
        conn.execute(f"""ATTACH ':memory:' AS "{ice_name}" """)
        conn.execute(f'CREATE SCHEMA "{ice_name}"."public"')
        conn.execute(f'CREATE TABLE "{ice_name}"."public"."t" AS SELECT 1 AS x')

        # With the view
        views = [
            {
                "database": "development",
                "schema_name": "public",
                "name": "v",
                "view_sql": 'SELECT * FROM "development"."public"."t"',
            }
        ]
        _setup_overlay(conn, "development", ice_name, views=views)
        assert conn.execute('SELECT x FROM "development"."public"."v"').fetchone() == (1,)

        # After DROP VIEW — re-setup overlay without the view
        conn.execute('DETACH "development"')
        _setup_overlay(conn, "development", ice_name, views=[])
        with pytest.raises(duckdb.CatalogException):
            conn.execute('SELECT x FROM "development"."public"."v"')


# ---------------------------------------------------------------------------
# dbt Flow: CREATE TABLE → pass-through view, CREATE VIEW → stored view
# ---------------------------------------------------------------------------

class TestDbtSessionViewFlow:
    """Simulates what happens during a dbt run.

    dbt flow:
    1. Backend creates worker session with all databases + overlay (force_overlay=True)
    2. dbt CREATE TABLE → backend rewrites to _ice_ prefix → worker creates table
       → backend sends CREATE VIEW for pass-through
    3. dbt CREATE VIEW → backend stores in PG → forwards to worker session
    4. Later queries resolve through the overlay
    """

    @pytest.fixture
    def conn(self):
        c = duckdb.connect()
        yield c
        c.close()

    def test_dbt_creates_table_with_passthrough_view(self, conn):
        """dbt CREATE TABLE → _ice_ rewrite → pass-through view in overlay."""
        # Worker session starts with overlay (force_overlay=True)
        ice_name = "_ice_development"
        conn.execute(f"""ATTACH ':memory:' AS "{ice_name}" """)
        conn.execute(f'CREATE SCHEMA "{ice_name}"."dbt_user"')

        # Overlay is set up at session creation with no views initially
        _setup_overlay(conn, "development", ice_name, views=[])

        # dbt sends: CREATE TABLE development.dbt_user.my_model AS SELECT ...
        # Backend rewrites to: CREATE TABLE _ice_development.dbt_user.my_model AS SELECT ...
        conn.execute(
            f'CREATE TABLE "{ice_name}"."dbt_user"."my_model" AS '
            f"SELECT 1 AS id, 'hello' AS value"
        )

        # Backend then sends: CREATE VIEW development.dbt_user.my_model AS
        #   SELECT * FROM _ice_development.dbt_user.my_model
        conn.execute(
            'CREATE OR REPLACE VIEW "development"."dbt_user"."my_model" AS '
            f'SELECT * FROM "{ice_name}"."dbt_user"."my_model"'
        )

        # Now the table is queryable through the user-facing name
        result = conn.execute(
            'SELECT id, value FROM "development"."dbt_user"."my_model"'
        ).fetchall()
        assert result == [(1, "hello")]

    def test_dbt_creates_view_materialization(self, conn):
        """dbt materialized='view' → CREATE VIEW stored in PG, registered in overlay."""
        ice_name = "_ice_development"
        conn.execute(f"""ATTACH ':memory:' AS "{ice_name}" """)
        conn.execute(f'CREATE SCHEMA "{ice_name}"."dbt_user"')
        conn.execute(
            f'CREATE TABLE "{ice_name}"."dbt_user"."source_data" AS '
            f"SELECT 1 AS id, 100 AS amount UNION ALL SELECT 2, 200"
        )

        _setup_overlay(conn, "development", ice_name, views=[])

        # dbt sends: CREATE VIEW development.dbt_user.my_view AS SELECT ...
        # Backend intercepts, stores in PG, and forwards to worker session.
        # Worker session receives the CREATE VIEW directly (overlay accepts it).
        conn.execute(
            'CREATE VIEW "development"."dbt_user"."my_view" AS '
            'SELECT id, amount * 2 AS doubled FROM "development"."dbt_user"."source_data"'
        )

        result = conn.execute(
            'SELECT id, doubled FROM "development"."dbt_user"."my_view" ORDER BY id'
        ).fetchall()
        assert result == [(1, 200), (2, 400)]

    def test_dbt_drop_table_removes_passthrough(self, conn):
        """dbt DROP TABLE → _ice_ rewrite + drop pass-through view."""
        ice_name = "_ice_development"
        conn.execute(f"""ATTACH ':memory:' AS "{ice_name}" """)
        conn.execute(f'CREATE SCHEMA "{ice_name}"."dbt_user"')
        conn.execute(
            f'CREATE TABLE "{ice_name}"."dbt_user"."old_model" AS SELECT 1 AS x'
        )

        _setup_overlay(conn, "development", ice_name, views=[])

        # Create pass-through (simulating what happens after CREATE TABLE)
        conn.execute(
            'CREATE OR REPLACE VIEW "development"."dbt_user"."old_model" AS '
            f'SELECT * FROM "{ice_name}"."dbt_user"."old_model"'
        )

        # Verify it works
        assert conn.execute('SELECT x FROM "development"."dbt_user"."old_model"').fetchone() == (1,)

        # dbt DROP TABLE → backend rewrites to _ice_ + drops pass-through
        conn.execute(f'DROP TABLE "{ice_name}"."dbt_user"."old_model"')
        conn.execute('DROP VIEW IF EXISTS "development"."dbt_user"."old_model"')

        # Now it's gone
        with pytest.raises(duckdb.CatalogException):
            conn.execute('SELECT x FROM "development"."dbt_user"."old_model"')

    def test_dbt_create_schema_in_both_databases(self, conn):
        """dbt CREATE SCHEMA → backend forwards to both _ice_ and overlay."""
        ice_name = "_ice_development"
        conn.execute(f"""ATTACH ':memory:' AS "{ice_name}" """)
        _setup_overlay(conn, "development", ice_name, views=[])

        # Backend intercepts CREATE SCHEMA and sends to both databases
        conn.execute(f'CREATE SCHEMA "{ice_name}"."new_schema"')
        conn.execute('CREATE SCHEMA "development"."new_schema"')

        # Both work: can create table in _ice_ and view in overlay
        conn.execute(
            f'CREATE TABLE "{ice_name}"."new_schema"."t" AS SELECT 42 AS val'
        )
        conn.execute(
            'CREATE VIEW "development"."new_schema"."t" AS '
            f'SELECT * FROM "{ice_name}"."new_schema"."t"'
        )

        result = conn.execute('SELECT val FROM "development"."new_schema"."t"').fetchone()
        assert result == (42,)

    def test_dbt_full_session_lifecycle(self, conn):
        """Full dbt run: create schema, create table, create view, query both."""
        ice_name = "_ice_development"
        conn.execute(f"""ATTACH ':memory:' AS "{ice_name}" """)
        conn.execute(f'CREATE SCHEMA "{ice_name}"."dbt_user"')
        _setup_overlay(conn, "development", ice_name, views=[])

        # dbt creates a schema (forwarded to both)
        conn.execute('CREATE SCHEMA IF NOT EXISTS "development"."dbt_user"')

        # dbt creates a table model (rewritten to _ice_ + pass-through)
        conn.execute(
            f'CREATE TABLE "{ice_name}"."dbt_user"."customers" AS '
            f"SELECT 1 AS id, 'Alice' AS name UNION ALL SELECT 2, 'Bob'"
        )
        conn.execute(
            'CREATE OR REPLACE VIEW "development"."dbt_user"."customers" AS '
            f'SELECT * FROM "{ice_name}"."dbt_user"."customers"'
        )

        # dbt creates a view model (stored in PG, forwarded to session)
        conn.execute(
            'CREATE VIEW "development"."dbt_user"."customer_count" AS '
            'SELECT count(*) AS cnt FROM "development"."dbt_user"."customers"'
        )

        # Query the table model
        result = conn.execute(
            'SELECT name FROM "development"."dbt_user"."customers" ORDER BY name'
        ).fetchall()
        assert result == [("Alice",), ("Bob",)]

        # Query the view model
        result = conn.execute(
            'SELECT cnt FROM "development"."dbt_user"."customer_count"'
        ).fetchone()
        assert result == (2,)


# ---------------------------------------------------------------------------
# Multi-Database Views (cross-database references)
# ---------------------------------------------------------------------------

class TestMultiDatabaseViews:
    """Views can reference tables in other databases."""

    @pytest.fixture
    def conn(self):
        c = duckdb.connect()
        yield c
        c.close()

    def test_view_references_another_database(self, conn):
        """A view in 'development' references a table in 'retail_sales'."""
        # Set up two "Iceberg" databases
        conn.execute("""ATTACH ':memory:' AS "_ice_development" """)
        conn.execute('CREATE SCHEMA "_ice_development"."public"')

        conn.execute("""ATTACH ':memory:' AS "_ice_retail_sales" """)
        conn.execute('CREATE SCHEMA "_ice_retail_sales"."customers"')
        conn.execute(
            'CREATE TABLE "_ice_retail_sales"."customers"."loyalty" AS '
            "SELECT 1 AS customer_id, 500 AS points"
        )

        # Set up overlays for both databases
        _setup_overlay(conn, "retail_sales", "_ice_retail_sales", views=[])
        views = [
            {
                "database": "development",
                "schema_name": "public",
                "name": "loyalty_summary",
                "view_sql": 'SELECT * FROM "retail_sales"."customers"."loyalty"',
            }
        ]
        _setup_overlay(conn, "development", "_ice_development", views=views)

        result = conn.execute(
            'SELECT customer_id, points FROM "development"."public"."loyalty_summary"'
        ).fetchall()
        assert result == [(1, 500)]
