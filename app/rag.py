"""RAG: chunk text, embed, index into document_chunks; hybrid retrieve + local rerank."""

import re
import uuid

import structlog
from sqlalchemy import delete, select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.llm import embed
from app.models import EMBED_DIM, Document, DocumentChunk

log = structlog.get_logger()

CHUNK_CHARS = 1200
CHUNK_OVERLAP = 150

# Hybrid retrieval: vector candidates + keyword candidates are unioned before rerank.
VECTOR_CANDIDATES = 12
KEYWORD_CANDIDATES = 12
RERANK_CANDIDATES = 24
FINAL_TOP_K = 6
RERANK_MIN_SCORE = 0.05  # cross-encoder scores ~[-10, 10]; sigmoid keeps confident hits


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Chunk on sentence boundaries where possible, with `overlap`-char carryover.

    Falls back to whitespace-splitting inside oversized sentences (no mid-word cuts).
    """
    text = " ".join(text.split())
    if not text:
        return []
    if len(text) <= size:
        return [text]

    # Sentence spans (Devanagari danda included); whitespace-normalised text keeps ". " etc.
    sentences: list[str] = []
    start = 0
    for m in re.finditer(r"[.!?।]\s", text):
        end = m.end()
        sentences.append(text[start:end])
        start = end
    sentences.append(text[start:])

    chunks: list[str] = []
    cur = ""
    for sent in sentences:
        # Oversized single sentence: hard-split it on spaces.
        while len(sent) > size:
            cut = sent.rfind(" ", size // 2, size)
            if cut <= 0:
                cut = size
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(sent[:cut].strip())
            sent = sent[cut:].strip()
        if not sent:
            continue
        candidate = f"{cur} {sent}".strip() if cur else sent
        if len(candidate) <= size:
            cur = candidate
        else:
            if cur:
                chunks.append(cur)
            # carry the tail of cur for overlap
            cur = (cur[-overlap:] + " " + sent).strip() if overlap else sent
            if len(cur) > size:
                cur = sent
    if cur:
        chunks.append(cur)
    # Drop tiny trailing fragments by merging into the previous chunk.
    merged: list[str] = []
    for c in chunks:
        if merged and len(c) < 40:
            merged[-1] = f"{merged[-1]} {c}".strip()
        else:
            merged.append(c)
    return merged


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


async def _vector_candidates(session: AsyncSession, user_id: uuid.UUID,
                             qvec: list[float], k: int,
                             document_id: uuid.UUID | None) -> list[DocumentChunk]:
    stmt = (
        select(DocumentChunk)
        .where(DocumentChunk.user_id == user_id)
        .order_by(DocumentChunk.embedding.cosine_distance(qvec))
        .limit(k)
    )
    if document_id is not None:
        stmt = stmt.where(DocumentChunk.document_id == document_id)
    rows = list((await session.scalars(stmt)).all())
    return [r for r in rows if r.embedding is not None and len(r.embedding) == EMBED_DIM]


async def _keyword_candidates(session: AsyncSession, user_id: uuid.UUID,
                              query: str, k: int,
                              document_id: uuid.UUID | None) -> list[DocumentChunk]:
    """Postgres full-text match against chunk content (websearch_to_tsquery, english)."""
    where = ["user_id = :uid",
             "content_tsv @@ websearch_to_tsquery('english', :q)"]
    params: dict = {"uid": str(user_id), "q": query, "k": k}
    if document_id is not None:
        where.append("document_id = :did")
        params["did"] = str(document_id)
    sql = sql_text(
        "SELECT id FROM document_chunks WHERE " + " AND ".join(where) +
        " ORDER BY ts_rank(content_tsv, websearch_to_tsquery('english', :q)) DESC LIMIT :k"
    )
    rows = (await session.execute(sql, params)).fetchall()
    if not rows:
        return []
    ids = [r[0] for r in rows]
    return list((await session.scalars(
        select(DocumentChunk).where(DocumentChunk.id.in_(ids))
    )).all())


async def rerank(query: str, docs: list[str]) -> list[float]:
    """Local cross-encoder rerank (bge-reranker-v2-m3). Returns a score per doc.

    Lazy-loads the model on first call; scores are sigmoid-normalised to [0, 1].
    """
    if not docs:
        return []
    from app import reranker  # deferred import: torch load must not happen at module import

    return await reranker.score(query, docs)


async def retrieve(
    session: AsyncSession, user_id: uuid.UUID, query: str, top_k: int = FINAL_TOP_K,
    document_id: uuid.UUID | None = None,
) -> list[DocumentChunk]:
    """Hybrid retrieval: vector + keyword candidates, reranked by the local cross-encoder.

    Falls back to plain cosine ranking when the reranker is disabled or unavailable.
    """
    if not query.strip():
        return []
    s = get_settings()
    qvec = (await embed(session, user_id, [query]))[0]

    vec_hits = await _vector_candidates(session, user_id, qvec, VECTOR_CANDIDATES, document_id)
    kw_hits = await _keyword_candidates(session, user_id, query, KEYWORD_CANDIDATES, document_id)

    # Union preserving order (vector hits first, keyword-only hits appended), deduped.
    seen: set[uuid.UUID] = set()
    candidates: list[DocumentChunk] = []
    for c in [*vec_hits, *kw_hits]:
        if c.id not in seen:
            seen.add(c.id)
            candidates.append(c)
    if not candidates:
        return []
    if len(candidates) <= top_k:
        return candidates

    if s.rerank_enabled:
        try:
            scores = await rerank(query, [c.content for c in candidates])
            ranked = sorted(zip(candidates, scores, strict=True), key=lambda p: -p[1])
            ranked = [(c, sc) for c, sc in ranked if sc >= RERANK_MIN_SCORE]
            return [c for c, _ in ranked[:top_k]] or candidates[:top_k]
        except Exception as e:  # reranker must never break chat
            log.warning("rag.rerank_failed", error=str(e))
    return candidates[:top_k]
