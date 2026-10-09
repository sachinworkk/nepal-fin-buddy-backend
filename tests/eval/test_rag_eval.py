"""Eval harness for the RAG chatbot, built on deepeval.

Everything here makes real LLM calls: needs OPENROUTER_API_KEY + a reachable
Postgres (tests use finbuddy_test, migrated by tests/conftest.py).
Run with:  uv run pytest tests/eval -m live_llm -q
Never runs in the default suite (pytest.ini filters live_llm out).
Cost note: each case makes 1 chat call + 1 embed call + several judge calls;
the whole suite is ~13 cases. EVAL_JUDGE_MODEL picks the judge (default:
openai/gpt-oss-120b via OpenRouter).

All fixtures/tests run on the pytest-asyncio session loop — do not call
asyncio.run() here; asyncpg connections are loop-bound.
"""

from collections.abc import Awaitable, Callable

import pytest
from deepeval import assert_test
from deepeval.metrics import (
    AnswerRelevancyMetric,
    ContextualPrecisionMetric,
    ContextualRecallMetric,
    FaithfulnessMetric,
    GEval,
)
from deepeval.models import OpenRouterModel
from deepeval.test_case import LLMTestCase, LLMTestCaseParams

from app.config import get_settings
from tests.eval.golden import golden_cases, seed_documents

pytestmark = pytest.mark.live_llm


@pytest.fixture(scope="session")
def judge():
    """The LLM deepeval uses to score answers. A strong model is recommended;
    override with EVAL_JUDGE_MODEL in .env."""
    s = get_settings()
    if not s.openrouter_api_key:
        pytest.skip("OPENROUTER_API_KEY not set")
    return OpenRouterModel(model=s.eval_judge_model, api_key=s.openrouter_api_key)


def _chunks(text: str, size: int = 1200) -> list[str]:
    flat = " ".join(text.split())
    if not flat:
        return [""]
    return [flat[i:i + size] for i in range(0, len(flat), size)]


@pytest.fixture
async def corpus(app_engine):
    """Seed the golden corpus with REAL embeddings. Function-scoped: the shared
    `client` fixture truncates all tables after every test, so session-scoped
    seeds would vanish. 4 embed calls (one per document) per test."""
    import uuid

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.core.security import hash_password
    from app.llm import embed
    from app.models import Document, DocumentChunk, User

    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    async with maker() as session:
        user_id = uuid.uuid4()
        session.add(User(id=user_id, email=f"{user_id}@eval.local",
                         password_hash=hash_password("eval-password")))
        await session.flush()
        s = get_settings()
        for kind, title, text in seed_documents():
            doc = Document(user_id=user_id, kind=kind, title=title, text_content=text)
            session.add(doc)
            await session.flush()
            chunks = _chunks(doc.text_content)
            vecs = await embed(session, user_id, chunks)
            for i, (chunk, vec) in enumerate(zip(chunks, vecs, strict=True)):
                session.add(DocumentChunk(
                    document_id=doc.id, user_id=user_id, chunk_index=i,
                    content=chunk, embedding=vec, embedding_model=s.embed_model,
                    meta={"title": doc.title, "document_kind": doc.kind},
                ))
        await session.commit()
    return user_id


@pytest.fixture
def retrieval(corpus, app_engine) -> Callable[[str], Awaitable[list[str]]]:
    """Async callable: query -> retrieved chunk texts (real embeddings)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.rag import retrieve

    maker = async_sessionmaker(app_engine, expire_on_commit=False)

    async def get(query: str, top_k: int = 6) -> list[str]:
        async with maker() as session:
            chunks = await retrieve(session, corpus, query, top_k=top_k)
            return [c.content for c in chunks]

    return get


@pytest.fixture
def chat_client(client, corpus):
    """Async callable: question -> (answer, citation snippets) via real /chat.

    Authenticates as the corpus user: the golden documents are seeded under that user, so any
    other user would see an empty library and every answer would be "I couldn't find ...".
    """
    from app.core.security import create_access_token

    auth = {"Authorization": f"Bearer {create_access_token(corpus)}"}
    session_holder: dict[str, str] = {}

    async def ask(question: str) -> tuple[str, list[str]]:
        if not session_holder:
            r = await client.post("/chat/sessions", headers=auth)
            assert r.status_code == 201, r.text
            session_holder["id"] = r.json()["id"]
        r = await client.post(f"/chat/sessions/{session_holder['id']}/messages",
                              headers=auth, json={"content": question})
        assert r.status_code == 200, r.text
        body = r.json()
        snippets = [c["snippet"] for c in (body.get("citations") or [])]
        return body["content"], snippets

    return ask


class TestRetrievalQuality:
    """Does vector search surface the right document for each question?"""

    # Unanswerable questions have no right context, so precision/recall is undefined;
    # they are covered by the distance test below and by end-to-end faithfulness.
    @pytest.mark.parametrize("case", [c for c in golden_cases() if c["must_retrieve"]],
                             ids=lambda c: c["id"])
    async def test_context_precision_recall(self, case, retrieval, judge):
        contexts = await retrieval(case["question"])
        assert contexts, f"no context retrieved for {case['id']}"
        tc = LLMTestCase(
            input=case["question"],
            actual_output=case["expected_answer_hint"],
            expected_output=case["expected_answer_hint"],
            retrieval_context=contexts,
        )
        assert_test(tc, [
            ContextualPrecisionMetric(model=judge, threshold=0.7),
            ContextualRecallMetric(model=judge, threshold=0.7),
        ])

    async def test_refusal_case_retrieves_nothing_strongly_relevant(
        self, retrieval, corpus, app_engine,
    ):
        """retrieve() always returns top-k, so for an out-of-corpus question we
        assert the best match is only weakly similar instead of empty."""
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.llm import embed
        from app.models import DocumentChunk

        case = next(c for c in golden_cases() if c["id"] == "missing_info_refusal")
        _ = await retrieval(case["question"])  # exercise retrieval, ignore its output
        maker = async_sessionmaker(app_engine, expire_on_commit=False)
        async with maker() as session:
            qvec = (await embed(session, corpus, [case["question"]]))[0]
            row = (await session.execute(
                select(
                    DocumentChunk, DocumentChunk.embedding.cosine_distance(qvec).label("d"),
                ).where(DocumentChunk.user_id == corpus)
                .order_by("d").limit(1)
            )).first()
            assert row is not None
            best, distance = row
        distance = float(distance)
        assert distance > 0.5, (
            f"fuel question matched '{best.meta.get('title')}' too closely "
            f"(cosine distance {distance:.2f}) — retrieval may be leaking irrelevant docs"
        )


class TestEndToEnd:
    """Full pipeline: question -> /chat -> real answer -> answer-quality metrics."""

    @pytest.mark.parametrize("case", golden_cases(), ids=lambda c: c["id"])
    async def test_chat_answer_quality(self, case, chat_client, judge):
        answer, snippets = await chat_client(case["question"])
        tc = LLMTestCase(
            input=case["question"], actual_output=answer,
            expected_output=case["expected_answer_hint"],
            retrieval_context=snippets,
        )
        correctness = GEval(
            name="Correctness", model=judge, threshold=0.7,
            criteria=(
                "Judge whether the actual output gives the same answer as the expected output. "
                "The headline fact(s) in the expected output (the main amount, name or date the "
                "question asks for) must be present and must not be contradicted. Formatting "
                "(Rs. 20,000 vs Rs. 20,000.00, Nepali vs Western numerals), synonyms "
                "(Supermarket vs grocery) and extra detail are fine, and so is leaving out "
                "secondary details. Fail an output that gives a wrong or invented value, or the "
                "wrong figure (e.g. gross instead of net). If the expected output says the "
                "information is not available, the actual output must say it has no such "
                "information or record; a generic refusal to help, or any invented value, fails."
            ),
            evaluation_params=[LLMTestCaseParams.ACTUAL_OUTPUT, LLMTestCaseParams.EXPECTED_OUTPUT],
        )
        metrics = [FaithfulnessMetric(model=judge, threshold=0.8, include_reason=True), correctness]
        if case["must_retrieve"]:
            # A correct refusal is inherently "irrelevant" to the question, so only score
            # relevancy where the answer is in the corpus.
            metrics.append(AnswerRelevancyMetric(model=judge, threshold=0.7))
        assert_test(tc, metrics)