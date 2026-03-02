"""Change hourly_rate_cents to hourly_rate_eur decimal

Revision ID: 4f2ba4b5dc69
Revises: 4bc08fa7bea2
Create Date: 2026-03-02 15:43:46.971479

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4f2ba4b5dc69'
down_revision: Union[str, None] = '4bc08fa7bea2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new column as nullable first
    op.add_column(
        'server_type_rates',
        sa.Column('hourly_rate_eur', sa.Numeric(precision=10, scale=4), nullable=True),
    )
    # Convert cents to EUR (e.g. 4 -> 0.0400)
    op.execute("UPDATE server_type_rates SET hourly_rate_eur = hourly_rate_cents / 100.0")
    # Make non-nullable and drop old column
    op.alter_column('server_type_rates', 'hourly_rate_eur', nullable=False)
    op.drop_column('server_type_rates', 'hourly_rate_cents')


def downgrade() -> None:
    op.add_column(
        'server_type_rates',
        sa.Column('hourly_rate_cents', sa.INTEGER(), nullable=True),
    )
    op.execute("UPDATE server_type_rates SET hourly_rate_cents = ROUND(hourly_rate_eur * 100)")
    op.alter_column('server_type_rates', 'hourly_rate_cents', nullable=False)
    op.drop_column('server_type_rates', 'hourly_rate_eur')
