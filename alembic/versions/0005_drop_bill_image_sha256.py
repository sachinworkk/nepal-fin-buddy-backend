"""drop bills.image_sha256 (upload dedupe removed)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(op.f('ix_bills_image_sha256'), table_name='bills')
    op.drop_column('bills', 'image_sha256')


def downgrade() -> None:
    op.add_column('bills', sa.Column('image_sha256', sa.String(length=64), nullable=True))
    op.create_index(op.f('ix_bills_image_sha256'), 'bills', ['image_sha256'], unique=False)
