"""SQL tools + the Strands chat agent (memory, expense writes, confirmations), with the
model and OpenRouter stubbed out."""

import datetime as dt
import json
import uuid

import pytest
from sqlalchemy import select, text

from app import assistant, llm
from app.agent import model as agent_model
from app.assistant import AggregateArgs, ListArgs
from app.models import AgentStorage, ChatMessage, Expense
from app.services import prize as prize_service
from tests.integration.fake_agent import FakeAgentModel
from tests.integration.test_prize import TODAY, _confirmed_bill, _user_id, handoff_body
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


@pytest.fixture(autouse=True)
def _pin_prize_today(monkeypatch):
    monkeypatch.setattr(prize_service, "nepal_today", lambda: TODAY)


@pytest.fixture
def agent(monkeypatch):
    """Script the chat model: agent([{"tools": [...]}, {"text": ...}, ...])."""
    def make(script, default="ok"):
        monkeypatch.setattr(llm, "_post", FakeLLM()._post)  # embeddings for retrieval
        m = FakeAgentModel(script, default=default)
        monkeypatch.setattr(agent_model, "build_model", lambda: m)
        return m
    return make


async def _new_session(client, auth) -> str:
    return (await client.post("/chat/sessions", headers=auth)).json()["id"]


async def _say(client, auth, sid, content):
    return await client.post(f"/chat/sessions/{sid}/messages", headers=auth,
                             json={"content": content})


async def _ask(client, auth, content="total groceries?"):
    sid = await _new_session(client, auth)
    return sid, await _say(client, auth, sid, content)


async def _decide(client, auth, sid, decisions):
    return await client.post(f"/chat/sessions/{sid}/actions", headers=auth, json={
        "decisions": [{"interrupt_id": i, "decision": d} for i, d in decisions]})


async def _expense_id(client, auth) -> str:
    return (await client.get("/expenses", headers=auth)).json()[0]["id"]


ANSWER = {"text": "Result: {last_tool}"}


async def test_agent_round_trip_persists_tool_rows(client, auth, agent, db):
    await add(client, auth, "120.00")
    m = agent([{"tools": [("aggregate_expenses", {
        "metric": "sum", "category": "groceries", "user_id": str(uuid.uuid4())})]}, ANSWER])
    sid, r = await _ask(client, auth)
    assert r.status_code == 200, r.text
    assert "120.00" in r.json()["content"]
    assert "Today is" in m.calls[0][0]["content"]  # system prompt carries today's date
    tool_rows = list((await db.execute(
        select(ChatMessage).where(ChatMessage.role == "tool"))).scalars())
    assert [t.tool_name for t in tool_rows] == ["aggregate_expenses"]
    # tool rows stay out of the visible transcript
    detail = (await client.get(f"/chat/sessions/{sid}", headers=auth)).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]


async def test_agent_uses_only_callers_expenses(client, auth, other_auth, agent):
    await add(client, other_auth, "777.00")
    agent([{"tools": [("aggregate_expenses", {})]}, ANSWER])
    _, r = await _ask(client, auth)
    assert "777" not in r.json()["content"]


async def test_agent_lists_prize_eligible_bills(client, auth, other_auth, agent, db):
    user = await _user_id(auth)
    await _confirmed_bill(db, user, bill_number="BILL-OK")
    await _confirmed_bill(db, user, bill_number="BILL-BADPAN", vendor_pan="12345")
    await _confirmed_bill(db, await _user_id(other_auth), bill_number="BILL-OTHER")
    agent([{"tools": [("list_prize_eligible_bills", {})]}, ANSWER])
    _, r = await _ask(client, auth, "which bills are eligible for the IRD lottery?")
    assert r.status_code == 200, r.text
    content = r.json()["content"]
    assert "BILL-OK" in content
    assert "BILL-BADPAN" not in content and "BILL-OTHER" not in content


async def test_agent_lists_handed_off_bills_as_awaiting(client, auth, agent, db):
    bill = await _confirmed_bill(db, await _user_id(auth), bill_number="BILL-HANDOFF",
                                 vendor_name="Bhatbhateni")
    r = await client.post("/prize/enrollments/handoff", headers=auth,
                          json=handoff_body(str(bill.id)))
    assert r.status_code == 200, r.text
    agent([{"tools": [("list_prize_eligible_bills", {})]}, ANSWER])
    _, r = await _ask(client, auth, "which bills are eligible for the IRD lottery?")
    assert r.status_code == 200, r.text
    out = json.loads(r.json()["content"].removeprefix("Result: "))
    [b] = out["eligible_bills"]
    assert b["bill_number"] == "BILL-HANDOFF" and b["vendor"] == "Bhatbhateni"
    assert b["status"] == "awaiting_portal_submission"


async def test_invalid_tool_args_reach_model_as_text(client, auth, agent):
    agent([{"tools": [("list_expenses", {"limit": 500})]}, ANSWER])
    _, r = await _ask(client, auth)
    assert r.status_code == 200
    assert "Invalid arguments" in r.json()["content"]


async def test_unknown_tool_does_not_500(client, auth, agent):
    agent([{"tools": [("drop_tables", {}), ("list_expenses", {})]}, ANSWER])
    _, r = await _ask(client, auth)
    assert r.status_code == 200


async def test_sql_answers_do_not_attach_unrelated_citations(client, auth, agent):
    await client.post("/documents", headers=auth, json={"title": "Note", "text": "rent note"})
    await add(client, auth, "10.00")
    agent([{"tools": [("aggregate_expenses", {})]}, ANSWER])
    _, r = await _ask(client, auth)
    assert r.json()["citations"] == []


async def test_memory_spans_turns_but_not_sessions(client, auth, agent):
    await add(client, auth, "321.00", merchant="Roadhouse")
    m = agent([{"tools": [("list_expenses", {})]}, ANSWER, {"text": "second"},
               {"text": "other session"}])
    sid, _ = await _ask(client, auth, "what did I spend?")
    await _say(client, auth, sid, "and where was that?")
    # the second turn's model call still sees the first turn's tool call and result
    second = "\n".join(x["content"] for x in m.calls[2])
    assert "list_expenses" in second and "Roadhouse" in second
    assert "what did I spend?" in second

    other = await _new_session(client, auth)
    await _say(client, auth, other, "anything?")
    fresh = m.calls[3]
    assert [x["role"] for x in fresh] == ["system", "user"]
    assert "Roadhouse" not in fresh[1]["content"]


async def test_deleting_session_drops_agent_memory(client, auth, agent, db):
    agent([{"text": "hi"}])
    sid, _ = await _ask(client, auth, "hello")
    keys = lambda: db.scalars(select(AgentStorage.key).where(  # noqa: E731
        AgentStorage.key.contains(sid)))
    assert list(await keys())
    assert (await client.delete(f"/chat/sessions/{sid}", headers=auth)).status_code == 204
    assert list(await keys()) == []


async def test_create_expense_from_chat(client, auth, other_auth, agent):
    agent([{"tools": [("create_expense", {
        "amount": "450", "category": "dining", "merchant": "Roadhouse", "note": "momo",
        "user_id": "nope"})]}, ANSWER])
    _, r = await _ask(client, auth, "I spent Rs 450 on momo at Roadhouse today")
    assert r.status_code == 200, r.text
    assert "created" in r.json()["content"]
    rows = (await client.get("/expenses", headers=auth)).json()
    assert len(rows) == 1
    e = rows[0]
    assert (e["amount"], e["category"], e["merchant"], e["source"]) == (
        "450.00", "dining", "Roadhouse", "chat")
    assert e["expense_date_ad"] == dt.date.today().isoformat()
    assert (await client.get("/expenses", headers=other_auth)).json() == []


async def test_create_expense_rejects_bad_category(client, auth, agent):
    agent([{"tools": [("create_expense", {"amount": "10", "category": "yachts"})]}, ANSWER])
    _, r = await _ask(client, auth, "add it")
    assert r.status_code == 200
    assert (await client.get("/expenses", headers=auth)).json() == []


async def test_delete_waits_for_confirmation(client, auth, agent, db):
    await add(client, auth, "99.00")
    eid = await _expense_id(client, auth)
    agent([{"tools": [("delete_expense", {"expense_id": eid})]}, ANSWER])
    sid, r = await _ask(client, auth, "delete it")
    assert r.status_code == 200, r.text
    [action] = r.json()["pending_actions"]
    assert action["action"] == "delete" and action["expense"]["id"] == eid
    assert action["decision"] is None
    assert (await client.get(f"/expenses/{eid}", headers=auth)).status_code == 200

    # nothing else may happen in the chat until the user decides
    assert (await _say(client, auth, sid, "hello?")).status_code == 409

    r = await _decide(client, auth, sid, [(action["interrupt_id"], "confirm")])
    assert r.status_code == 200, r.text
    assert "deleted" in r.json()["content"]
    assert (await client.get(f"/expenses/{eid}", headers=auth)).status_code == 404
    detail = (await client.get(f"/chat/sessions/{sid}", headers=auth)).json()
    assert detail["messages"][1]["pending_actions"][0]["decision"] == "confirm"
    tools = list(await db.scalars(select(ChatMessage.tool_name).where(
        ChatMessage.role == "tool")))
    assert tools == ["delete_expense"]
    # and the chat is unblocked again
    assert (await _say(client, auth, sid, "thanks")).status_code == 200


async def test_update_cancel_then_confirm(client, auth, agent):
    await add(client, auth, "100.00")
    eid = await _expense_id(client, auth)
    upd = {"tools": [("update_expense", {"expense_id": eid, "amount": "500"})]}
    agent([upd, ANSWER, upd, ANSWER])
    sid, r = await _ask(client, auth, "change it to 500")
    [action] = r.json()["pending_actions"]
    assert action["changes"] == {"amount": "500"}
    assert action["expense"]["amount"] == "100.00"
    r = await _decide(client, auth, sid, [(action["interrupt_id"], "cancel")])
    assert "cancelled" in r.json()["content"]
    assert (await client.get(f"/expenses/{eid}", headers=auth)).json()["amount"] == "100.00"

    r = await _say(client, auth, sid, "actually yes, change it")
    [action] = r.json()["pending_actions"]
    await _decide(client, auth, sid, [(action["interrupt_id"], "confirm")])
    assert (await client.get(f"/expenses/{eid}", headers=auth)).json()["amount"] == "500.00"


async def test_cancel_does_not_reprompt(client, auth, agent):
    await add(client, auth, "50.00")
    eid = await _expense_id(client, auth)
    dele = {"tools": [("delete_expense", {"expense_id": eid})]}
    # After the cancel the model tries to delete again; that must not raise a new prompt.
    agent([dele, dele, {"text": "Okay, I cancelled that."}])
    sid, r = await _ask(client, auth, "delete it")
    [action] = r.json()["pending_actions"]
    r = await _decide(client, auth, sid, [(action["interrupt_id"], "cancel")])
    assert r.json()["pending_actions"] is None
    assert r.json()["content"] == "Okay, I cancelled that."
    assert (await client.get(f"/expenses/{eid}", headers=auth)).status_code == 200
    assert (await _say(client, auth, sid, "thanks")).status_code == 200


async def test_cannot_touch_other_users_expense(client, auth, other_auth, agent):
    await add(client, other_auth, "55.00")
    eid = await _expense_id(client, other_auth)
    agent([{"tools": [("delete_expense", {"expense_id": eid})]}, ANSWER])
    _, r = await _ask(client, auth, "delete it")
    assert r.json()["pending_actions"] is None
    assert "not found" in r.json()["content"]
    assert (await client.get(f"/expenses/{eid}", headers=other_auth)).status_code == 200


async def test_actions_endpoint_rejects_bad_decisions(client, auth, other_auth, agent, db):
    await add(client, auth, "10.00")
    eid = await _expense_id(client, auth)
    sid = await _new_session(client, auth)
    agent([{"tools": [("delete_expense", {"expense_id": eid})]}, ANSWER])
    assert (await _decide(client, auth, sid, [("x", "confirm")])).status_code == 409
    r = await _say(client, auth, sid, "delete it")
    [action] = r.json()["pending_actions"]
    assert (await _decide(client, auth, sid, [("x", "confirm")])).status_code == 409
    assert (await _decide(client, other_auth, sid,
                          [(action["interrupt_id"], "confirm")])).status_code == 404
    assert await db.scalar(select(Expense.id).where(Expense.id == uuid.UUID(eid)))


async def test_set_and_get_budgets_from_chat(client, auth, other_auth, agent):
    agent([{"tools": [("set_budget", {"category": "dining", "amount": "8000",
                                      "user_id": "nope"})]}, ANSWER])
    _, r = await _ask(client, auth, "set my dining budget to 8000")
    assert r.status_code == 200, r.text
    [b] = (await client.get("/budgets", headers=auth)).json()
    assert (b["category"], b["amount"]) == ("dining", "8000.00")
    assert (await client.get("/budgets", headers=other_auth)).json() == []


async def test_set_budget_rejects_bad_input(client, auth, agent):
    agent([{"tools": [("set_budget", {"category": "dining", "amount": "-1"}),
                      ("set_budget", {"category": "yachts", "amount": "100"})]}, ANSWER])
    _, r = await _ask(client, auth, "set budgets")
    assert r.status_code == 200
    assert (await client.get("/budgets", headers=auth)).json() == []


async def test_get_budgets_tool_reports_spend(client, auth, agent):
    await client.put("/budgets/groceries", headers=auth, json={"amount": "1000"})
    await add(client, auth, "900.00", day=dates_today())
    seen = []

    def step(messages):
        seen.append(messages[-1]["content"])
        return {"text": "done"}
    agent([{"tools": [("get_budgets", {})]}, step])
    _, r = await _ask(client, auth, "how is my grocery budget?")
    assert r.status_code == 200, r.text
    assert '"status": "warning"' in seen[0] and '"spent": "900.00"' in seen[0]


def dates_today() -> dt.date:
    from tests.integration.test_stats import bs_first_day
    return bs_first_day(0)
