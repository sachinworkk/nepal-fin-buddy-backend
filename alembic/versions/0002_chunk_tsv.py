"""add content_tsv tsvector to document_chunks for hybrid keyword retrieval

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS content_tsv tsvector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    # Keep content_tsv in sync on insert/update; simplest reliable path is a trigger
    # so any writer (ORM or raw SQL) keeps the index correct.
    op.execute("""
        CREATE OR REPLACE FUNCTION document_chunks_tsv_update() RETURNS trigger AS $$
        BEGIN
            NEW.content_tsv := to_tsvector('english', COALESCE(NEW.content, ''));
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    op.execute("DROP TRIGGER IF EXISTS trg_document_chunks_tsv ON document_chunks")
    op.execute(
        "CREATE TRIGGER trg_document_chunks_tsv BEFORE INSERT OR UPDATE OF content "
        "ON document_chunks FOR EACH ROW EXECUTE FUNCTION document_chunks_tsv_update()"
    )
    # Backfill existing rows (trigger covers new inserts only).
    op.execute("UPDATE document_chunks SET content_tsv = to_tsvector('english', content)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_document_chunks_tsv "
        "ON document_chunks USING gin (content_tsv)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_tsv")
    op.execute("DROP TRIGGER IF EXISTS trg_document_chunks_tsv ON document_chunks")
    op.execute("DROP FUNCTION IF EXISTS document_chunks_tsv_update()")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS content_tsv")
