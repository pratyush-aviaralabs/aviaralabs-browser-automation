"""allow duplicate external_invoice_id values on invoices

Revision ID: 504c3a8de95b
Revises: ea5b91c2dcbb
Create Date: 2026-11-18 20:05:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '504c3a8de95b'
down_revision: Union[str, None] = 'ea5b91c2dcbb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Confirmed required (2026-11-18): duplicate invoices are now
    # accepted deliberately — a resent/duplicate invoice_id must create
    # its own row and run through the pipeline again, not get rejected
    # by this unique index. Dropped and recreated non-unique so lookups
    # by external_invoice_id (if any are ever added) stay indexed.
    op.drop_index(op.f('ix_invoices_external_invoice_id'), table_name='invoices')
    op.create_index(op.f('ix_invoices_external_invoice_id'), 'invoices', ['external_invoice_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_invoices_external_invoice_id'), table_name='invoices')
    op.create_index(op.f('ix_invoices_external_invoice_id'), 'invoices', ['external_invoice_id'], unique=True)
