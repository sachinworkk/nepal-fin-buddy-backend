"""Integration tests for RAG indexing + chat, with OpenRouter stubbed out."""


import pytest

from app import llm
from app.models import DocumentChunk


def _fake_vec(seed: int) -> list[float]:
    v = [0.0] * 1024
    v[seed % 1024] = 1.0
    return v


class FakeLLM:
    """Deterministic stand-in for the OpenRouter calls in app.llm."""

    def __init__(self):
        self.embed_calls: list[list[str]] = []
        self.chat_calls: list[list[dict]] = []

    async def _post(self, session, purpose, user_id, path, body):
        if path == "/embeddings":
            texts = body["input"]
            self.embed_calls.append(texts)
            await llm._log(session, purpose, body.get("model"), None, None, None, None,
                           None, None, "ok", 200, None, 1)
            return {"data": [
                {"embedding": _fake_vec(sum(map(ord, t)))} for t in texts
            ]}
        if path == "/chat/completions":
            self.chat_calls.append(body["messages"])
            await llm._log(session, purpose, body.get("model"), "gen-123", 100, 10,
                           5, None, None, "ok", 200, None, 1)
            return {"choices": [{"message": {"content": "The rent bill was Rs. 20,000."}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 10},
                    "id": "gen-123"}
        raise AssertionError(path)


@pytest.fixture
def fake_llm(monkeypatch):
    f = FakeLLM()
    monkeypatch.setattr(llm, "_post", f._post)
    return f


async def _upload_note(client, auth, text: str, title: str = "Rent note") -> str:
    r = await client.post("/documents", headers=auth, json={"title": title, "text": text})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_note_upload_indexes_chunks(client, auth, fake_llm, db):
    doc_id = await _upload_note(
        client, auth, "Paid rent Rs. 20000 for Shrawan. " * 300,
    )  # ~9k chars -> several 1200-char chunks
    chunks = (await db.execute(
        DocumentChunk.__table__.select().where(DocumentChunk.document_id == doc_id)
    )).fetchall()
    assert len(chunks) > 1
    assert fake_llm.embed_calls and fake_llm.embed_calls[0]
    for row in chunks:
        assert len(row.embedding) == 1024


async def test_chat_uses_rag_context_and_cites(client, auth, fake_llm, db):
    await _upload_note(client, auth, "Paid rent Rs. 20000 for Shrawan to landlord Kumar.")
    r = await client.post("/chat/sessions", headers=auth)
    assert r.status_code == 201
    sid = r.json()["id"]

    r = await client.post(f"/chat/sessions/{sid}/messages", headers=auth,
                          json={"content": "How much rent did I pay?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["role"] == "assistant"
    assert "20,000" in body["content"]
    assert body["citations"], "expected citations from the indexed note"
    assert body["citations"][0]["title"] == "Rent note"

    # the chat call must have carried the retrieved context
    msgs = fake_llm.chat_calls[-1]
    context_msgs = [m for m in msgs if m["role"] == "system" and "Context from" in m["content"]]
    assert context_msgs and "rent" in context_msgs[0]["content"].lower()


async def test_chat_records_llm_call_log(client, auth, fake_llm, db):
    from sqlalchemy import select

    from app.models import LlmCallLog
    sid = (await client.post("/chat/sessions", headers=auth)).json()["id"]
    await client.post(f"/chat/sessions/{sid}/messages", headers=auth,
                      json={"content": "hi"})
    logs = list((await db.execute(select(LlmCallLog))).scalars())
    purposes = {lg.purpose for lg in logs}
    assert {"embedding", "chat"} <= purposes
    assert all(lg.status == "ok" for lg in logs)


async def test_chat_history_is_persisted(client, auth, fake_llm):
    sid = (await client.post("/chat/sessions", headers=auth)).json()["id"]
    await client.post(f"/chat/sessions/{sid}/messages", headers=auth, json={"content": "q1"})
    detail = (await client.get(f"/chat/sessions/{sid}", headers=auth)).json()
    roles = [m["role"] for m in detail["messages"]]
    assert roles == ["user", "assistant"]


async def test_session_isolation(client, auth, other_auth, fake_llm):
    sid = (await client.post("/chat/sessions", headers=auth)).json()["id"]
    r = await client.post(f"/chat/sessions/{sid}/messages", headers=other_auth,
                          json={"content": "sneak"})
    assert r.status_code == 404
    assert (await client.get(f"/chat/sessions/{sid}", headers=other_auth)).status_code == 404


async def test_retrieve_filters_by_user(client, auth, other_auth, fake_llm, db):
    from app.rag import retrieve

    await _upload_note(client, auth, "unique voucher text alpha bravo")
    rows = await retrieve(db, __import__("uuid").uuid4(), "voucher", top_k=3)
    assert rows == []