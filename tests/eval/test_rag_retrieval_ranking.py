"""Retrieval ranking metrics (hit@k) + reranker on/off benchmark.

Live-LLM suite: needs OPENROUTER_API_KEY (real embeddings). When RERANK_ENABLED=1
the local reranker model is used (first run downloads ~1.1GB). Run:
  uv run pytest tests/eval/test_rag_retrieval_ranking.py -m live_llm -q
Baseline run without reranking (compare the printed hit@k):
  RERANK_ENABLED=0 uv run pytest tests/eval/test_rag_retrieval_ranking.py -m live_llm -q
"""

import pytest

from app.config import get_settings
from tests.eval.golden import golden_cases, seed_documents

pytestmark = pytest.mark.live_llm

TOP_K = 6


@pytest.fixture
async def corpus(app_engine):
    """Seed the golden corpus with real embeddings; returns (user_id, maker)."""
    import uuid

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.llm import embed
    from app.models import Document, DocumentChunk, User

    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    async with maker() as session:
        user_id = uuid.uuid4()
        session.add(User(id=user_id, email=f"{user_id}@eval.ranking",
                         password_hash=hash_password("eval-password"),
                         full_name="Eval Ranking User"))
        await session.flush()
        s = get_settings()
        for kind, title, text in seed_documents():
            doc = Document(user_id=user_id, kind=kind, title=title, text_content=text)
            session.add(doc)
            await session.flush()
            chunks = [c for c in (part.strip() for part in text.split(".")) if c]
            vecs = await embed(session, user_id, chunks)
            for i, (chunk, vec) in enumerate(zip(chunks, vecs, strict=True)):
                session.add(DocumentChunk(
                    document_id=doc.id, user_id=user_id, chunk_index=i,
                    content=chunk, embedding=vec, embedding_model=s.embed_model,
                    meta={"title": doc.title, "document_kind": doc.kind},
                ))
        await session.commit()
    return user_id, maker


@pytest.fixture
def rank(corpus):
    """Callable: (question, k) -> list of chunk titles in retrieval order."""
    user_id, maker = corpus

    async def get(question: str, k: int = TOP_K) -> list[str]:
        from app.rag import retrieve

        async with maker() as session:
            chunks = await retrieve(session, user_id, question, top_k=k)
            return [c.meta.get("title", "?") for c in chunks]

    return get


def _hit(titles: list[str], want: str, k: int) -> bool:
    return want in titles[:k]


class TestHitRate:
    @pytest.mark.parametrize("case", [c for c in golden_cases() if c["must_retrieve"]],
                             ids=lambda c: c["id"])
    async def test_correct_document_in_top_k(self, case, rank):
        titles = await rank(case["question"], TOP_K)
        assert titles, f"nothing retrieved for {case['id']}"
        assert _hit(titles, case["must_retrieve"], TOP_K), (
            f"{case['id']}: expected '{case['must_retrieve']}' in top {TOP_K}, got {titles} "
            f"(rerank_enabled={get_settings().rerank_enabled})"
        )


class TestRerankerEffect:
    """A/B: run this module with RERANK_ENABLED=1 and RERANK_ENABLED=0 and compare the
    printed hit@k. This test also enforces a floor so a broken reranker fails loudly."""

    async def test_hit_rate_report(self, rank):
        cases = [c for c in golden_cases() if c["must_retrieve"]]
        hits = 0
        lines = []
        for case in cases:
            titles = await rank(case["question"], TOP_K)
            ok = _hit(titles, case["must_retrieve"], TOP_K)
            hits += ok
            lines.append(f"  {case['id']}: {'HIT ' if ok else 'MISS'} {titles}")
        rate = hits / len(cases)
        print(f"\nrerank_enabled={get_settings().rerank_enabled} "
              f"hit@{TOP_K}={hits}/{len(cases)} ({rate:.0%})")
        for line in lines:
            print(line)
        assert rate >= 0.8, f"hit@{TOP_K}={rate:.0%} below the 80% floor"