"""widen invoices.status to 40 chars

Revision ID: 05e3f853684f
Revises: dc6e8b84c508
Create Date: 2026-11-18 08:10:37.655602

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '05e3f853684f'
down_revision: Union[str, None] = 'dc6e8b84c508'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Autogenerate also proposed dropping twilio_*/messages/calls/users/
    # organizations — those belong to a different app sharing this
    # Supabase instance, not to our Base.metadata. Only the status
    # column widening below is this migration's actual intent.
    op.alter_column('invoices', 'status',
               existing_type=sa.VARCHAR(length=20),
               type_=sa.String(length=40),
               existing_nullable=False)


def downgrade() -> None:
    op.alter_column('invoices', 'status',
               existing_type=sa.String(length=40),
               type_=sa.VARCHAR(length=20),
               existing_nullable=False)
