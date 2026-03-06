"""End-to-end tests for the memory overlay pattern.

Tests that views registered via _setup_overlay() actually resolve queries
correctly — both pass-through views for tables and user-defined views.
No external services needed: uses :memory: databases to simulate Iceberg.
"""

import os
import sys

import duckdb
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from executor import _setup_overlay


@pytest.fixture
def conn():
    c = duckdb.connect()
    yield c
    c.close()


def _create_ice_db(conn, db_name="mydb"):
    """Simulate an Iceberg-ATTACHed database using :memory:."""
    ice_name = f"_ice_{db_name}"
    conn.execute(f"""ATTACH ':memory:' AS "{ice_name}" """)
    conn.execute(f'CREATE SCHEMA "{ice_name}"."myschema"')
    conn.execute(
        f'CREATE TABLE "{ice_name}"."myschema"."users" AS '
        f"SELECT 1 AS id, 'Alice' AS name UNION ALL SELECT 2, 'Bob'"
    )
    conn.execute(
        f'CREATE TABLE "{ice_name}"."myschema"."orders" AS '
        f"SELECT 100 AS order_id, 1 AS user_id, 50.0 AS amount "
        f"UNION ALL SELECT 101, 2, 75.0"
    )
    return ice_name


class TestOverlayPassthroughViews:
    """Pass-through views for tables should resolve correctly."""

    def test_query_table_through_overlay(self, conn):
        ice_name = _create_ice_db(conn)
        _setup_overlay(conn, "mydb", ice_name, views=[])
        result = conn.execute('SELECT * FROM "mydb"."myschema"."users" ORDER BY id').fetchall()
        assert result == [(1, "Alice"), (2, "Bob")]

    def test_query_multiple_tables(self, conn):
        ice_name = _create_ice_db(conn)
        _setup_overlay(conn, "mydb", ice_name, views=[])
        users = conn.execute('SELECT count(*) FROM "mydb"."myschema"."users"').fetchone()[0]
        orders = conn.execute('SELECT count(*) FROM "mydb"."myschema"."orders"').fetchone()[0]
        assert users == 2
        assert orders == 2

    def test_join_through_overlay(self, conn):
        ice_name = _create_ice_db(conn)
        _setup_overlay(conn, "mydb", ice_name, views=[])
        result = conn.execute(
            'SELECT u.name, o.amount FROM "mydb"."myschema"."users" u '
            'JOIN "mydb"."myschema"."orders" o ON u.id = o.user_id '
            "ORDER BY u.name"
        ).fetchall()
        assert result == [("Alice", 50.0), ("Bob", 75.0)]


class TestOverlayUserViews:
    """User-defined views should be registered and queryable."""

    def test_simple_view(self, conn):
        ice_name = _create_ice_db(conn)
        views = [
            {
                "database": "mydb",
                "schema_name": "myschema",
                "name": "user_count",
                "view_sql": 'SELECT count(*) AS cnt FROM "mydb"."myschema"."users"',
            }
        ]
        _setup_overlay(conn, "mydb", ice_name, views=views)
        result = conn.execute('SELECT cnt FROM "mydb"."myschema"."user_count"').fetchone()
        assert result == (2,)

    def test_view_referencing_multiple_tables(self, conn):
        ice_name = _create_ice_db(conn)
        views = [
            {
                "database": "mydb",
                "schema_name": "myschema",
                "name": "user_totals",
                "view_sql": (
                    "SELECT u.name, sum(o.amount) AS total "
                    'FROM "mydb"."myschema"."users" u '
                    'JOIN "mydb"."myschema"."orders" o ON u.id = o.user_id '
                    "GROUP BY u.name"
                ),
            }
        ]
        _setup_overlay(conn, "mydb", ice_name, views=views)
        result = conn.execute(
            'SELECT name, total FROM "mydb"."myschema"."user_totals" ORDER BY name'
        ).fetchall()
        assert result == [("Alice", 50.0), ("Bob", 75.0)]

    def test_view_in_new_schema(self, conn):
        """Views can be in schemas that don't exist in the Iceberg database."""
        ice_name = _create_ice_db(conn)
        views = [
            {
                "database": "mydb",
                "schema_name": "analytics",
                "name": "summary",
                "view_sql": "SELECT 42 AS answer",
            }
        ]
        _setup_overlay(conn, "mydb", ice_name, views=views)
        result = conn.execute('SELECT answer FROM "mydb"."analytics"."summary"').fetchone()
        assert result == (42,)

    def test_view_overrides_table(self, conn):
        """A user view with the same name as a table replaces the pass-through."""
        ice_name = _create_ice_db(conn)
        views = [
            {
                "database": "mydb",
                "schema_name": "myschema",
                "name": "users",
                "view_sql": "SELECT 999 AS id, 'Override' AS name",
            }
        ]
        _setup_overlay(conn, "mydb", ice_name, views=views)
        result = conn.execute('SELECT * FROM "mydb"."myschema"."users"').fetchall()
        assert result == [(999, "Override")]

    def test_view_referencing_another_view(self, conn):
        """Views can reference other user-defined views (order matters)."""
        ice_name = _create_ice_db(conn)
        views = [
            {
                "database": "mydb",
                "schema_name": "myschema",
                "name": "active_users",
                "view_sql": 'SELECT * FROM "mydb"."myschema"."users" WHERE id = 1',
            },
            {
                "database": "mydb",
                "schema_name": "myschema",
                "name": "active_user_names",
                "view_sql": 'SELECT name FROM "mydb"."myschema"."active_users"',
            },
        ]
        _setup_overlay(conn, "mydb", ice_name, views=views)
        result = conn.execute('SELECT name FROM "mydb"."myschema"."active_user_names"').fetchall()
        assert result == [("Alice",)]

    def test_invalid_view_sql_does_not_crash(self, conn):
        """A view with broken SQL should be skipped, not crash the overlay setup."""
        ice_name = _create_ice_db(conn)
        views = [
            {
                "database": "mydb",
                "schema_name": "myschema",
                "name": "broken",
                "view_sql": "SELECT * FROM nonexistent_table_xyz",
            },
            {
                "database": "mydb",
                "schema_name": "myschema",
                "name": "good",
                "view_sql": "SELECT 1 AS x",
            },
        ]
        _setup_overlay(conn, "mydb", ice_name, views=views)
        # The good view should still work
        result = conn.execute('SELECT x FROM "mydb"."myschema"."good"').fetchone()
        assert result == (1,)

    def test_empty_views_list(self, conn):
        """Overlay with no views still creates working pass-through."""
        ice_name = _create_ice_db(conn)
        _setup_overlay(conn, "mydb", ice_name, views=[])
        result = conn.execute('SELECT count(*) FROM "mydb"."myschema"."users"').fetchone()
        assert result == (2,)


class TestOverlayWithEmptyDatabase:
    """Edge case: Iceberg database with no schemas or tables."""

    def test_empty_ice_db_with_view(self, conn):
        conn.execute("""ATTACH ':memory:' AS "_ice_empty" """)
        views = [
            {
                "database": "empty",
                "schema_name": "public",
                "name": "constants",
                "view_sql": "SELECT 3.14 AS pi",
            }
        ]
        _setup_overlay(conn, "empty", "_ice_empty", views=views)
        result = conn.execute('SELECT pi FROM "empty"."public"."constants"').fetchone()
        assert float(result[0]) == pytest.approx(3.14)
