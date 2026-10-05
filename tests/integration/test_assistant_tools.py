"""SQL tools + tool-calling loop, with OpenRouter stubbed out."""

import datetime as dt
import json
import uuid

import pytest
from sqlalchemy import select, text

from app import assistant, llm
from app.assistant import AggregateArgs, ListArgs
from app.models import ChatMessage
from tests.integration.test_rag_chat import FakeLLM

D = dt.date(2026, 9, 10)


async def add(client, auth, amount, category="groceries", merchant="Bhatbhateni", day=D):
    r = await client.post("/expenses", headers=auth, json={
        "amount": amount, "category": category, "merchant": merchant,
        "expense_date": day.isoformat()})
    assert r.status_code == 201, r.text


async def _uid(client, auth) -> uuid.UUID:
    return uuid.UUID((await client.get("/auth/me", headers=auth)).json()["id"])


async def test_aggregate_sum_and_group(client, auth, db):
    await add(client, auth, "100.00")
    await add(client, auth, "50.50", merchant="BigMart")
    await add(client, auth, "300.00", category="dining", merchant="Roadhouse")
    uid = await _uid(client, auth)

    total = await assistant.aggregate_expenses(db, uid, AggregateArgs())
    assert total["value"] == "450.50"

    by_cat = await assistant.aggregate_expenses(
        db, uid, AggregateArgs(group_by="category", metric="sum"))
    assert by_cat["rows"][0] == {"group": "dining", "value": "300.00"}
    assert {"group": "groceries", "value": "150.50"} in by_cat["rows"]

    count = await assistant.aggregate_expenses(
        db, uid, AggregateArgs(metric="count", category="groceries"))
    assert count["value"] == "2" or count["value"] == 2

    by_month = await assistant.aggregate_expenses(db, uid, AggregateArgs(group_by="month"))
    assert by_month["rows"] == [{"group": "2026-09", "value": "450.50"}]


async def test_filters_date_and_merchant(client, auth, db):
    await add(client, auth, "10.00", day=dt.date(2026, 8, 1))
    await add(client, auth, "20.00", day=dt.date(2026, 9, 1))
    uid = await _uid(client, auth)
    r = await assistant.aggregate_expenses(
        db, uid, AggregateArgs(date_from=dt.date(2026, 9, 1), merchant="bhat"))
    assert r["value"] == "20.00"


async def test_list_expenses_limit_and_order(client, auth, db):
    for i in range(3):
        await add(client, auth, f"{i + 1}.00", day=D + dt.timedelta(days=i))
    uid = await _uid(client, auth)
    r = await assistant.list_expenses(db, uid, ListArgs(limit=2))
    assert [e["amount"] for e in r["expenses"]] == ["3.00", "2.00"]


async def test_tools_are_user_scoped(client, auth, other_auth, db):
    await add(client, auth, "999.00")
    other = await _uid(client, other_auth)
    assert (await assistant.aggregate_expenses(db, other, AggregateArgs()))["value"] == "0"
    assert (await assistant.list_expenses(db, other, ListArgs()))["expenses"] == []


async def test_read_only_enforced(client, auth, db):
    with pytest.raises(Exception, match="read-only"):
        await assistant._read_only_rows(db, text("DELETE FROM expenses"))


class ToolLLM(FakeLLM):
    """Round 1: model asks for a tool call; round 2: answers from the tool result."""

    def __init__(self, calls: list[dict]):
        super().__init__()
        self.calls = calls

    async def _post(self, session, purpose, user_id, path, body):
        if path == "/chat/completions":
            self.chat_calls.append(body["messages"])
            await llm._log(session, purpose, body.get("model"), "g", 1, 1, 1, None, None,
                           "ok", 200, None, 1)
            last = body["messages"][-1]
            if last["role"] == "tool":
                return {"choices": [{"message": {"content": f"Result: {last['content']}"}}]}
            return {"choices": [{"message": {"content": "", "tool_calls": [
                {"id": f"c{i}", "type": "function", "function": c}
                for i, c in enumerate(self.calls)]}}]}
        return await super()._post(session, purpose, user_id, path, body)


@pytest.fixture
def tool_llm(monkeypatch):
    def make(calls):
        f = ToolLLM(calls)
        monkeypatch.setattr(llm, "_post", f._post)
        return f
    return make


async def _ask(client, auth, content="total groceries?"):
    sid = (await client.post("/chat/sessions", headers=auth)).json()["id"]
    r = await client.post(f"/chat/sessions/{sid}/messages", headers=auth,
                          json={"content": content})
    return sid, r


async def test_agent_round_trip_persists_tool_rows(client, auth, tool_llm, db):
    await add(client, auth, "120.00")
    f = tool_llm([{"name": "aggregate_expenses", "arguments": json.dumps(
        {"metric": "sum", "category": "groceries", "user_id": str(uuid.uuid4())})}])
    sid, r = await _ask(client, auth)
    assert r.status_code == 200, r.text
    assert "120.00" in r.json()["content"]
    # tools were offered, and the system prompt carried today's date
    assert any("Today is" in m["content"] for m in f.chat_calls[0] if m["role"] == "system")
    tool_rows = list((await db.execute(
        select(ChatMessage).where(ChatMessage.role == "tool"))).scalars())
    assert [t.tool_name for t in tool_rows] == ["aggregate_expenses"]
    assert "user_id" not in tool_rows[0].tool_args
    # tool rows stay out of the visible transcript
    detail = (await client.get(f"/chat/sessions/{sid}", headers=auth)).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]


async def test_agent_uses_only_callers_expenses(client, auth, other_auth, tool_llm):
    await add(client, other_auth, "777.00")
    tool_llm([{"name": "aggregate_expenses", "arguments": "{}"}])
    _, r = await _ask(client, auth)
    assert "777" not in r.json()["content"]


async def test_invalid_tool_args_reach_model_as_text(client, auth, tool_llm):
    tool_llm([{"name": "list_expenses", "arguments": json.dumps({"limit": 500})}])
    _, r = await _ask(client, auth)
    assert r.status_code == 200
    assert "Invalid arguments" in r.json()["content"]


async def test_unknown_tool_and_bad_json_do_not_500(client, auth, tool_llm):
    tool_llm([{"name": "drop_tables", "arguments": "{}"},
              {"name": "list_expenses", "arguments": "{not json"}])
    _, r = await _ask(client, auth)
    assert r.status_code == 200


async def test_sql_answers_do_not_attach_unrelated_citations(client, auth, tool_llm):
    await client.post("/documents", headers=auth, json={"title": "Note", "text": "rent note"})
    await add(client, auth, "10.00")
    tool_llm([{"name": "aggregate_expenses", "arguments": "{}"}])
    _, r = await _ask(client, auth)
    assert r.json()["citations"] == []
