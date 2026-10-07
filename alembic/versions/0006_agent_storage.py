"""agent_storage for Strands session snapshots; chat_messages.pending_actions

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0006'
down_revision: Union[str, Sequence[str], None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'agent_storage',
        sa.Column('key', sa.String(length=500), nullable=False),
        sa.Column('data', sa.LargeBinary(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )
    op.add_column('chat_messages', sa.Column(
        'pending_actions', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'),
        nullable=True))


def downgrade() -> None:
    op.drop_column('chat_messages', 'pending_actions')
    op.drop_table('agent_storage')
