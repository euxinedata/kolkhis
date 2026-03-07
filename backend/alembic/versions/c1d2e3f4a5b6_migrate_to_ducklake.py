"""Migrate from Iceberg/Lakekeeper to DuckLake

Revision ID: c1d2e3f4a5b6
Revises: b177e5da219b
Create Date: 2026-03-07 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, None] = 'b177e5da219b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns to org_databases
    op.add_column('org_databases', sa.Column('data_path', sa.String(length=512), nullable=True))
    op.add_column('org_databases', sa.Column('metadata_schema', sa.String(length=255), nullable=True))

    # Populate new columns from existing data
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, org_id, name FROM org_databases")).fetchall()
    for row in rows:
        org_id = row[1]
        db_name = row[2]
        short_org = org_id.split("-")[0]
        data_path = f"s3://pontus-dev-iceberg/{org_id}/{db_name}/"
        metadata_schema = f"ducklake_{short_org}_{db_name}"
        conn.execute(
            sa.text("UPDATE org_databases SET data_path = :dp, metadata_schema = :ms WHERE id = :id"),
            {"dp": data_path, "ms": metadata_schema, "id": row[0]},
        )

    # Make new columns non-nullable
    op.alter_column('org_databases', 'data_path', nullable=False)
    op.alter_column('org_databases', 'metadata_schema', nullable=False)

    # Drop old column
    op.drop_column('org_databases', 'lakekeeper_warehouse')

    # Drop org_views table (views now stored in DuckLake natively)
    op.drop_index(op.f('ix_org_views_org_id'), table_name='org_views')
    op.drop_table('org_views')


def downgrade() -> None:
    # Recreate org_views table
    op.create_table('org_views',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('org_id', sa.String(length=36), nullable=False),
        sa.Column('database', sa.String(length=255), nullable=False),
        sa.Column('schema_name', sa.String(length=255), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('view_sql', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'database', 'schema_name', 'name'),
    )
    op.create_index(op.f('ix_org_views_org_id'), 'org_views', ['org_id'], unique=False)

    # Re-add lakekeeper_warehouse column
    op.add_column('org_databases', sa.Column('lakekeeper_warehouse', sa.String(length=255), nullable=True))

    # Populate from existing data
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, org_id, name FROM org_databases")).fetchall()
    for row in rows:
        warehouse_name = f"{row[1]}-{row[2]}"
        conn.execute(
            sa.text("UPDATE org_databases SET lakekeeper_warehouse = :wn WHERE id = :id"),
            {"wn": warehouse_name, "id": row[0]},
        )

    op.alter_column('org_databases', 'lakekeeper_warehouse', nullable=False)

    # Drop new columns
    op.drop_column('org_databases', 'metadata_schema')
    op.drop_column('org_databases', 'data_path')
