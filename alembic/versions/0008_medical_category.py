"""rename the health category to medical

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-08
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0008'
down_revision: Union[str, Sequence[str], None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _rename(old: str, new: str, label_en: str, label_ne: str) -> None:
    # The slug is a foreign-key target: add the new row, repoint expenses, drop the old one.
    op.execute(f"""
        INSERT INTO categories (slug, label_en, label_ne, sort_order)
        SELECT '{new}', '{label_en}', '{label_ne}', sort_order FROM categories WHERE slug = '{old}'
    """)
    op.execute(f"UPDATE expenses SET category = '{new}' WHERE category = '{old}'")
    op.execute(f"DELETE FROM categories WHERE slug = '{old}'")


def upgrade() -> None:
    _rename('health', 'medical', 'Medical', 'चिकित्सा')


def downgrade() -> None:
    _rename('medical', 'health', 'Health', 'स्वास्थ्य')
