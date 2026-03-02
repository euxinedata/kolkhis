"""Add billing tables and destroyed_at to worker_vms

Revision ID: 4bc08fa7bea2
Revises: 337d984c219d
Create Date: 2026-03-02 14:51:15.382185

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4bc08fa7bea2'
down_revision: Union[str, None] = '337d984c219d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Column may already exist if create_all ran before migration
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name='worker_vms' AND column_name='destroyed_at'"
    ))
    if result.fetchone() is None:
        op.add_column('worker_vms', sa.Column('destroyed_at', sa.DateTime(), nullable=True))

    op.create_table(
        'usage_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('event_type', sa.String(length=30), nullable=False),
        sa.Column('server_type', sa.String(length=20), nullable=True),
        sa.Column('worker_vm_id', sa.Integer(), nullable=True),
        sa.Column('metadata_json', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        if_not_exists=True,
    )
    # Index may already exist
    conn = op.get_bind()
    result = conn.execute(sa.text(
        "SELECT 1 FROM pg_indexes WHERE indexname='ix_usage_events_user_id'"
    ))
    if result.fetchone() is None:
        op.create_index('ix_usage_events_user_id', 'usage_events', ['user_id'])

    op.create_table(
        'server_type_rates',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('server_type', sa.String(length=20), nullable=False, unique=True),
        sa.Column('hourly_rate_cents', sa.Integer(), nullable=False),
        sa.Column('display_name', sa.String(length=50), nullable=True),
        if_not_exists=True,
    )

    op.create_table(
        'billing_periods',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('period_start', sa.DateTime(), nullable=False),
        sa.Column('period_end', sa.DateTime(), nullable=False),
        sa.Column('compute_seconds', sa.Integer(), nullable=False),
        sa.Column('compute_cost_cents', sa.Integer(), nullable=False),
        sa.Column('storage_cost_cents', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('total_cost_cents', sa.Integer(), nullable=False),
        sa.Column('stripe_reported', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        if_not_exists=True,
    )
    result = conn.execute(sa.text(
        "SELECT 1 FROM pg_indexes WHERE indexname='ix_billing_periods_user_id'"
    ))
    if result.fetchone() is None:
        op.create_index('ix_billing_periods_user_id', 'billing_periods', ['user_id'])


def downgrade() -> None:
    op.drop_table('billing_periods')
    op.drop_table('server_type_rates')
    op.drop_table('usage_events')
    op.drop_column('worker_vms', 'destroyed_at')
