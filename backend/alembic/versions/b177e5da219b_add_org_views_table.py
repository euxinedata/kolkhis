"""Add org_views table

Revision ID: b177e5da219b
Revises: b3e0b47b8752
Create Date: 2026-03-06 20:17:22.325514

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b177e5da219b'
down_revision: Union[str, None] = 'b3e0b47b8752'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('org_views',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('database', sa.String(length=255), nullable=False),
    sa.Column('schema_name', sa.String(length=255), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('view_sql', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('org_id', 'database', 'schema_name', 'name')
    )
    op.create_index(op.f('ix_org_views_org_id'), 'org_views', ['org_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_org_views_org_id'), table_name='org_views')
    op.drop_table('org_views')
