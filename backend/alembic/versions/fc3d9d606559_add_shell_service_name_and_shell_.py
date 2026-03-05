"""Add shell_service_name and shell_provisions

Revision ID: fc3d9d606559
Revises: fbdf439112a7
Create Date: 2026-03-05 21:46:37.587139

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fc3d9d606559'
down_revision: Union[str, None] = 'fbdf439112a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('shell_provisions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('org_id', sa.String(length=36), nullable=False),
    sa.Column('action', sa.String(length=20), nullable=False),
    sa.Column('resource_type', sa.String(length=20), nullable=False),
    sa.Column('resource_name', sa.String(length=255), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_shell_provisions_org_id'), 'shell_provisions', ['org_id'], unique=False)
    op.add_column('organizations', sa.Column('shell_service_name', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('organizations', 'shell_service_name')
    op.drop_index(op.f('ix_shell_provisions_org_id'), table_name='shell_provisions')
    op.drop_table('shell_provisions')
