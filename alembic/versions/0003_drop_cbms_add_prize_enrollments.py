"""drop CBMS business-flow tables, add prize_enrollments for IRD prize lottery

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(op.f('ix_cbms_sync_queue_status'), table_name='cbms_sync_queue')
    op.drop_index(op.f('ix_cbms_sync_queue_next_attempt_at'), table_name='cbms_sync_queue')
    op.drop_table('cbms_sync_queue')
    op.drop_index(op.f('ix_issued_invoices_user_id'), table_name='issued_invoices')
    op.drop_table('issued_invoices')
    op.drop_table('ird_credentials')

    op.create_table('prize_enrollments',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('bill_id', sa.UUID(), nullable=True),
    sa.Column('seller_pan_no', sa.String(length=9), nullable=False),
    sa.Column('bill_number', sa.String(length=40), nullable=False),
    sa.Column('bill_date', sa.Date(), nullable=False),
    sa.Column('billed_total_amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('payment_method', sa.String(length=10), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('address', sa.String(length=200), nullable=False),
    sa.Column('mobile_number', sa.String(length=10), nullable=False),
    sa.Column('company_website', sa.String(length=200), nullable=True),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('prize_coupon_number', sa.String(length=12), nullable=True),
    sa.Column('verification_id', sa.String(length=30), nullable=True),
    sa.Column('duplicate', sa.Boolean(), nullable=False),
    sa.Column('ird_response', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('last_error', sa.String(length=500), nullable=True),
    sa.Column('form_started_at', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['bill_id'], ['bills.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_prize_enrollments_user_bill', 'prize_enrollments',
                    ['user_id', 'seller_pan_no', 'bill_number', 'bill_date'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_prize_enrollments_user_bill', table_name='prize_enrollments')
    op.drop_table('prize_enrollments')

    # Best-effort recreation of the removed CBMS-flow tables (empty; data is gone).
    op.create_table('ird_credentials',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('username_enc', sa.Text(), nullable=False),
    sa.Column('password_enc', sa.Text(), nullable=False),
    sa.Column('seller_pan', sa.String(length=20), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id')
    )
    op.create_table('issued_invoices',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('is_return', sa.Boolean(), nullable=False),
    sa.Column('invoice_number', sa.String(length=50), nullable=False),
    sa.Column('invoice_date_bs', sa.String(length=10), nullable=False),
    sa.Column('invoice_date_ad', sa.Date(), nullable=False),
    sa.Column('fiscal_year', sa.String(length=10), nullable=False),
    sa.Column('buyer_name', sa.String(length=300), nullable=False),
    sa.Column('buyer_pan', sa.String(length=20), nullable=True),
    sa.Column('total_sales', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('taxable_sales_vat', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('vat', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('excisable_amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('excise', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('taxable_sales_hst', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('hst', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('amount_for_esf', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('esf', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('export_sales', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('tax_exempted_sales', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('ref_invoice_number', sa.String(length=50), nullable=True),
    sa.Column('credit_note_number', sa.String(length=50), nullable=True),
    sa.Column('credit_note_date_bs', sa.String(length=10), nullable=True),
    sa.Column('reason_for_return', sa.String(length=300), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'fiscal_year', 'invoice_number', 'is_return', name='uq_issued_invoice_number')
    )
    op.create_index(op.f('ix_issued_invoices_user_id'), 'issued_invoices', ['user_id'], unique=False)
    op.create_table('cbms_sync_queue',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('issued_invoice_id', sa.UUID(), nullable=False),
    sa.Column('endpoint', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_response_code', sa.Integer(), nullable=True),
    sa.Column('last_error', sa.String(length=500), nullable=True),
    sa.Column('synced_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['issued_invoice_id'], ['issued_invoices.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('issued_invoice_id')
    )
    op.create_index(op.f('ix_cbms_sync_queue_next_attempt_at'), 'cbms_sync_queue', ['next_attempt_at'], unique=False)
    op.create_index(op.f('ix_cbms_sync_queue_status'), 'cbms_sync_queue', ['status'], unique=False)