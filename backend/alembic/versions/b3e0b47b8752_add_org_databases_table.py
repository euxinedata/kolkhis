"""Add org_databases table

Revision ID: b3e0b47b8752
Revises: fc3d9d606559
Create Date: 2026-03-06 08:26:09.640218

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3e0b47b8752'
down_revision: Union[str, None] = 'fc3d9d606559'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('org_databases',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('org_id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('lakekeeper_warehouse', sa.String(length=255), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'name'),
    )
    op.create_index(op.f('ix_org_databases_org_id'), 'org_databases', ['org_id'])


def downgrade() -> None:
    op.drop_index(op.f('ix_org_databases_org_id'), table_name='org_databases')
    op.drop_table('org_databases')
