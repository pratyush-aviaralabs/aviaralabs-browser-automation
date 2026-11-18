"""add external_invoice_id and callback_token to invoices

Revision ID: dc6e8b84c508
Revises: ea372f37bb91
Create Date: 2026-11-18 06:45:22.413905

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'dc6e8b84c508'
down_revision: Union[str, None] = 'ea372f37bb91'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Autogenerate also proposed dropping organizations/twilio_*/messages/
    # calls/users — those belong to a different app sharing this Supabase
    # instance, not to our Base.metadata. Only the invoices changes below
    # are this migration's actual intent.
    op.add_column('invoices', sa.Column('external_invoice_id', sa.String(length=100), nullable=True))
    op.add_column('invoices', sa.Column('callback_token', sa.String(length=255), nullable=True))
    op.create_index(op.f('ix_invoices_callback_token'), 'invoices', ['callback_token'], unique=False)
    op.create_index(op.f('ix_invoices_external_invoice_id'), 'invoices', ['external_invoice_id'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_invoices_external_invoice_id'), table_name='invoices')
    op.drop_index(op.f('ix_invoices_callback_token'), table_name='invoices')
    op.drop_column('invoices', 'callback_token')
    op.drop_column('invoices', 'external_invoice_id')
