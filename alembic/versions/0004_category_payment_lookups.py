"""category and payment-method lookup tables, bills.payment_method, one expense per bill

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CATEGORIES = [
    ('groceries', 'Groceries', 'किराना'),
    ('dining', 'Dining', 'खाना तथा रेस्टुरेन्ट'),
    ('transport', 'Transport', 'यातायात'),
    ('utilities', 'Utilities', 'उपयोगिता'),
    ('rent', 'Rent', 'भाडा'),
    ('health', 'Health', 'स्वास्थ्य'),
    ('education', 'Education', 'शिक्षा'),
    ('shopping', 'Shopping', 'किनमेल'),
    ('entertainment', 'Entertainment', 'मनोरञ्जन'),
    ('fees', 'Fees', 'शुल्क'),
    ('other', 'Other', 'अन्य'),
]
PAYMENT_METHODS = [
    ('cash', 'Cash', 'नगद', 'Cash'),
    ('card', 'Card', 'कार्ड', None),
    ('qr', 'QR', 'क्यूआर', None),
    ('cheque', 'Cheque', 'चेक', None),
]


def upgrade() -> None:
    categories = op.create_table('categories',
    sa.Column('slug', sa.String(length=30), nullable=False),
    sa.Column('label_en', sa.String(length=50), nullable=False),
    sa.Column('label_ne', sa.String(length=50), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('slug'),
    )
    methods = op.create_table('payment_methods',
    sa.Column('code', sa.String(length=10), nullable=False),
    sa.Column('label_en', sa.String(length=50), nullable=False),
    sa.Column('label_ne', sa.String(length=50), nullable=False),
    sa.Column('ird_code', sa.String(length=10), nullable=True),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('code'),
    )
    op.bulk_insert(categories, [
        dict(slug=s, label_en=en, label_ne=ne, sort_order=i)
        for i, (s, en, ne) in enumerate(CATEGORIES)
    ])
    op.bulk_insert(methods, [
        dict(code=c, label_en=en, label_ne=ne, ird_code=ird, sort_order=i)
        for i, (c, en, ne, ird) in enumerate(PAYMENT_METHODS)
    ])

    # Normalise existing free text onto the lookup values before adding the foreign keys.
    op.execute("""
        UPDATE expenses SET category = 'other'
        WHERE category NOT IN (SELECT slug FROM categories)
    """)
    op.execute("""
        UPDATE expenses SET payment_method = CASE
            WHEN lower(trim(payment_method)) IN ('cash', 'card', 'qr', 'cheque')
                THEN lower(trim(payment_method))
            WHEN lower(trim(payment_method)) = 'check' THEN 'cheque'
            WHEN lower(trim(payment_method)) IN ('esewa', 'khalti', 'fonepay', 'wallet') THEN 'qr'
            ELSE NULL
        END
        WHERE payment_method IS NOT NULL
    """)
    op.alter_column('expenses', 'payment_method', type_=sa.String(length=10))
    op.add_column('bills', sa.Column('payment_method', sa.String(length=10), nullable=True))

    op.create_foreign_key('fk_expenses_category', 'expenses', 'categories',
                          ['category'], ['slug'])
    op.create_foreign_key('fk_expenses_payment_method', 'expenses', 'payment_methods',
                          ['payment_method'], ['code'])
    op.create_foreign_key('fk_bills_payment_method', 'bills', 'payment_methods',
                          ['payment_method'], ['code'])
    op.create_index('uq_expenses_bill_id', 'expenses', ['bill_id'], unique=True,
                    postgresql_where=sa.text('bill_id IS NOT NULL'))


def downgrade() -> None:
    op.drop_index('uq_expenses_bill_id', table_name='expenses')
    op.drop_constraint('fk_bills_payment_method', 'bills', type_='foreignkey')
    op.drop_constraint('fk_expenses_payment_method', 'expenses', type_='foreignkey')
    op.drop_constraint('fk_expenses_category', 'expenses', type_='foreignkey')
    op.drop_column('bills', 'payment_method')
    op.alter_column('expenses', 'payment_method', type_=sa.String(length=30))
    op.drop_table('payment_methods')
    op.drop_table('categories')
