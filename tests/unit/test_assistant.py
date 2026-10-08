import datetime as dt

import pytest
from pydantic import ValidationError

from app.agent.tools import TOOLS
from app.assistant import AggregateArgs, ListArgs, SearchArgs, system_prompt


def test_aggregate_defaults():
    a = AggregateArgs()
    assert (a.metric, a.group_by, a.top_n) == ("sum", "none", 10)


@pytest.mark.parametrize("bad", [
    {"metric": "median"}, {"group_by": "user_id"}, {"category": "yachts"}, {"top_n": 21},
    {"top_n": 0}, {"date_from": "not-a-date"},
])
def test_aggregate_rejects_bad_args(bad):
    with pytest.raises(ValidationError):
        AggregateArgs(**bad)


@pytest.mark.parametrize("bad", [{"limit": 51}, {"limit": 0}, {"category": "x"}])
def test_list_rejects_bad_args(bad):
    with pytest.raises(ValidationError):
        ListArgs(**bad)


def test_search_requires_query():
    with pytest.raises(ValidationError):
        SearchArgs(query="")


def test_tools_expose_no_user_id():
    assert {t.tool_name for t in TOOLS} == {
        "aggregate_expenses", "list_expenses", "search_documents", "list_prize_eligible_bills",
        "create_expense", "update_expense", "delete_expense"}
    for t in TOOLS:
        props = t.tool_spec["inputSchema"]["json"]["properties"]
        assert not {"user_id", "tool_context", "document_ids"} & set(props), t.tool_name


def test_system_message_has_bs_month_ranges():
    import datetime as dt

    from app.assistant import bs_month_range, system_prompt

    today = dt.date(2026, 10, 5)  # Ashwin 2083
    start, end, label = bs_month_range(today, -1)  # Bhadra
    assert label == "2083-05"
    assert start < end < dt.date(2026, 9, 20) and (end - start).days in (28, 29, 30, 31)
    assert bs_month_range(today, 0)[0] == end + dt.timedelta(days=1)
    text = system_prompt(today)
    assert start.isoformat() in text and end.isoformat() in text


def test_system_prompt_restricts_scope():
    p = system_prompt(dt.date(2026, 10, 8))
    assert "I can only help with questions about your bills" in p
