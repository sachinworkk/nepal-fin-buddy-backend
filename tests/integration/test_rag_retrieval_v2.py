"""Tests for hybrid retrieval + local reranker wiring (reranker stubbed)."""


import uuid

import pytest
import pytest_asyncio

from app import rag
from app.config import get_settings
from app.models import Document, DocumentChunk, User


@pytest.fixture(autouse=True)
def _reset_reranker():
    from app import reranker

    reranker.reset()
    yield
    reranker.reset()


@pytest_asyncio.fixture
async def user(db):
    u = User(email=f"{uuid.uuid4().hex[:8]}@example.com", password_hash="x", full_name="Retrieval User")
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u


@pytest_asyncio.fixture
async def make_chunk(db, user):
    counter = {"n": 0}

    async def _make(content: str, document_id: uuid.UUID | None = None) -> DocumentChunk:
        counter["n"] += 1
        if document_id is None:
            doc = Document(user_id=user.id, kind="note", title=f"doc{counter['n']}",
                           text_content="")
            db.add(doc)
            await db.flush()
            document_id = doc.id
        c = DocumentChunk(
            document_id=document_id, user_id=user.id,
            chunk_index=counter["n"], content=content,
            embedding=[1.0] + [0.0] * 1023, embedding_model="fake",
        )
        db.add(c)
        await db.commit()
        return c

    return _make


@pytest.fixture
def fake_reranker(monkeypatch):
    """Reranker that scores docs by whether they contain the query's first word."""
    calls: list[tuple[str, list[str]]] = []

    async def score(query: str, docs: list[str]) -> list[float]:
        calls.append((query, docs))
        key = query.split()[0].lower()
        return [1.0 if key in d.lower() else 0.0 for d in docs]

    monkeypatch.setattr(rag, "rerank", score)
    return calls


async def test_retrieve_reranks_and_filters(make_chunk, fake_reranker, db, user):
    assert get_settings().rerank_enabled is True
    # 8 candidates so retrieve() goes through the rerank path (top_k=6).
    for i in range(8):
        await make_chunk(content=f"doc {i} about {'vat' if i % 2 else 'rent'} bill {i}")
    chunks = await rag.retrieve(db, user.id, "vat bill", top_k=6)
    assert fake_reranker, "reranker should have been called"
    # Only the 4 "vat" chunks score above the relevance threshold; the 4 "rent" chunks
    # are filtered out even though top_k=6.
    assert len(chunks) == 4
    for c in chunks:
        assert "vat" in c.content.lower()


async def test_retrieve_falls_back_when_reranker_fails(make_chunk, monkeypatch, db, user):
    async def boom(query, docs):
        raise RuntimeError("cuda down")

    monkeypatch.setattr(rag, "rerank", boom)
    for i in range(8):
        await make_chunk(content=f"doc {i} rent")
    chunks = await rag.retrieve(db, user.id, "rent", top_k=6)
    assert len(chunks) == 6  # fallback: candidate order, no exception raised


async def test_retrieve_skips_rerank_when_disabled(make_chunk, monkeypatch, db, user):
    monkeypatch.setenv("RERANK_ENABLED", "0")
    get_settings.cache_clear()
    try:
        calls: list = []

        async def score(query, docs):
            calls.append(1)
            return [0.0] * len(docs)

        monkeypatch.setattr(rag, "rerank", score)
        for i in range(8):
            await make_chunk(content=f"doc {i} rent")
        chunks = await rag.retrieve(db, user.id, "rent", top_k=6)
        assert not calls
        assert len(chunks) == 6
    finally:
        get_settings.cache_clear()


async def test_keyword_candidates_match_exact_terms(make_chunk, db, user):
    # Vector stub embeddings are one-hot by char-sum, so an exact-number keyword hit is the
    # only reliable way to find this chunk; proves the FTS leg works end to end.
    await make_chunk(content="PAN number 123456789 appears here")
    await make_chunk(content="totally unrelated words about rent")
    hits = await rag._keyword_candidates(db, user.id, "123456789", 5, None)
    assert len(hits) == 1
    assert "123456789" in hits[0].content
