"""add schedulers, scheduler_occurrences, scheduler_runs

Revision ID: ea5b91c2dcbb
Revises: 05e3f853684f
Create Date: 2026-11-18 10:17:08.811853

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'ea5b91c2dcbb'
down_revision: Union[str, None] = '05e3f853684f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Autogenerate also proposed dropping organizations/calls/messages/
    # twilio_*/users — those belong to a different app sharing this
    # Supabase instance, not to our Base.metadata. Only the three new
    # tables below are this migration's actual intent.
    op.create_table('schedulers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('target_status', sa.String(length=40), nullable=False),
    sa.Column('frequency_unit', sa.String(length=10), nullable=False),
    sa.Column('timezone', sa.String(length=50), nullable=False),
    sa.Column('last_run_at', sa.DateTime(), nullable=True),
    sa.Column('next_run_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_schedulers_next_run_at'), 'schedulers', ['next_run_at'], unique=False)
    op.create_table('scheduler_occurrences',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('scheduler_id', sa.Integer(), nullable=False),
    sa.Column('time_of_day', sa.String(length=5), nullable=False),
    sa.Column('day_of_week', sa.Integer(), nullable=True),
    sa.Column('day_of_month', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['scheduler_id'], ['schedulers.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('scheduler_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('scheduler_id', sa.Integer(), nullable=False),
    sa.Column('ran_at', sa.DateTime(), nullable=False),
    sa.Column('target_status', sa.String(length=40), nullable=False),
    sa.Column('invoice_ids_json', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['scheduler_id'], ['schedulers.id'], ),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('scheduler_runs')
    op.drop_table('scheduler_occurrences')
    op.drop_index(op.f('ix_schedulers_next_run_at'), table_name='schedulers')
    op.drop_table('schedulers')
