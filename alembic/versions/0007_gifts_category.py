"""gifts category for money given to family and friends

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-08
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0007'
down_revision: Union[str, Sequence[str], None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Slot gifts in just before "other", which stays last.
    op.execute("UPDATE categories SET sort_order = sort_order + 1 WHERE slug = 'other'")
    op.execute("""
        INSERT INTO categories (slug, label_en, label_ne, sort_order)
        SELECT 'gifts', 'Gifts & family', 'उपहार तथा परिवार', sort_order - 1
        FROM categories WHERE slug = 'other'
    """)


def downgrade() -> None:
    op.execute("UPDATE expenses SET category = 'other' WHERE category = 'gifts'")
    op.execute("DELETE FROM categories WHERE slug = 'gifts'")
    op.execute("UPDATE categories SET sort_order = sort_order - 1 WHERE slug = 'other'")
