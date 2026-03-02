"""Add worker_size to user_settings and server_type to worker_vms

Revision ID: 337d984c219d
Revises: 
Create Date: 2026-03-02 13:38:01.746350

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '337d984c219d'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('user_settings', sa.Column('worker_size', sa.String(length=20), server_default='cpx42', nullable=False))
    op.add_column('worker_vms', sa.Column('server_type', sa.String(length=20), server_default='cpx42', nullable=False))


def downgrade() -> None:
    op.drop_column('worker_vms', 'server_type')
    op.drop_column('user_settings', 'worker_size')
