"""Add shell_username to users

Revision ID: fbdf439112a7
Revises: 9796bf93c229
Create Date: 2026-03-03 15:35:38.810317

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fbdf439112a7'
down_revision: Union[str, None] = '9796bf93c229'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('shell_username', sa.String(length=32), nullable=True))
    op.create_unique_constraint('uq_users_shell_username', 'users', ['shell_username'])


def downgrade() -> None:
    op.drop_constraint('uq_users_shell_username', 'users', type_='unique')
    op.drop_column('users', 'shell_username')
