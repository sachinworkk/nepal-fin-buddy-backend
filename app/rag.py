"""RAG: chunk text, embed, index into document_chunks; retrieve by cosine similarity."""

import uuid

import structlog
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.llm import embed
from app.models import EMBED_DIM, Document, DocumentChunk

log = structlog.get_logger()

CHUNK_CHARS = 1200
CHUNK_OVERLAP = 150


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = " ".join(text.split())
    if not text:
        return []
    chunks: list[str] = []
    step = max(1, size - overlap)
    for i in range(0, len(text), step):
        part = text[i:i + size]
        if len(part) < 40 and chunks:
            break
        chunks.append(part)
        if i + size >= len(text):
            break
    return chunks


async def reindex_document(session: AsyncSession, doc: Document) -> int:
    """(Re)embed and index a document's text. Returns number of chunks indexed."""
    await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))
    texts = chunk_text(doc.text_content or "")
    if not texts:
        return 0
    vecs = await embed(session, doc.user_id, texts)
    for i, (t, v) in enumerate(zip(texts, vecs, strict=True)):
        session.add(DocumentChunk(
            document_id=doc.id, user_id=doc.user_id, chunk_index=i, content=t,
            embedding=v, embedding_model=get_settings().embed_model,
            meta={"document_kind": doc.kind, "title": doc.title},
        ))
    await session.commit()
    return len(texts)


async def index_document_safe(doc: Document) -> None:
    """Index a document into RAG in its own session; failures must not break the upload."""
    import structlog

    from app.db import SessionLocal
    from app.llm import LlmError

    if not (doc.text_content or "").strip():
        return
    try:
        async with SessionLocal() as s:
            await reindex_document(s, doc)
    except LlmError as e:
        structlog.get_logger().warning("rag.index_skipped", document_id=str(doc.id),
                                       error=str(e))


async def retrieve(
    session: AsyncSession, user_id: uuid.UUID, query: str, top_k: int = 6,
    document_id: uuid.UUID | None = None,
) -> list[DocumentChunk]:
    """Top-k chunks for a user by cosine similarity to the query embedding."""
    if not query.strip():
        return []
    qvec = (await embed(session, user_id, [query]))[0]
    stmt = (
        select(DocumentChunk)
        .where(DocumentChunk.user_id == user_id)
        .order_by(DocumentChunk.embedding.cosine_distance(qvec))
        .limit(top_k)
    )
    if document_id is not None:
        stmt = stmt.where(DocumentChunk.document_id == document_id)
    rows = list((await session.scalars(stmt)).all())
    return [r for r in rows if r.embedding is not None and len(r.embedding) == EMBED_DIM]