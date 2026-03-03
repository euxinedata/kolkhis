"""Add user_id to projects

Revision ID: 9796bf93c229
Revises: f52bf12c6bcd
Create Date: 2026-03-03 15:00:03.639164

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9796bf93c229'
down_revision: Union[str, None] = 'f52bf12c6bcd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add user_id as nullable first, backfill, then set NOT NULL
    op.add_column('projects', sa.Column('user_id', sa.Integer(), nullable=True))
    # Backfill: assign existing projects to user_id=0 (placeholder)
    op.execute("UPDATE projects SET user_id = 0 WHERE user_id IS NULL")
    op.alter_column('projects', 'user_id', nullable=False)
    op.drop_constraint(op.f('projects_name_key'), 'projects', type_='unique')
    op.create_unique_constraint('uq_projects_user_id_name', 'projects', ['user_id', 'name'])


def downgrade() -> None:
    op.drop_constraint('uq_projects_user_id_name', 'projects', type_='unique')
    op.create_unique_constraint(op.f('projects_name_key'), 'projects', ['name'])
    op.drop_column('projects', 'user_id')
