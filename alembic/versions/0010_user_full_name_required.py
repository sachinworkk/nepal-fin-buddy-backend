"""require full_name on users

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-10
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0010'
down_revision: Union[str, Sequence[str], None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE users SET full_name = '' WHERE full_name IS NULL")
    op.alter_column('users', 'full_name',
                    existing_type=sa.String(length=200),
                    nullable=False)


def downgrade() -> None:
    op.alter_column('users', 'full_name',
                    existing_type=sa.String(length=200),
                    nullable=True)
