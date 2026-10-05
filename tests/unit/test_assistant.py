import pytest
from pydantic import ValidationError

from app.assistant import TOOLS, AggregateArgs, ListArgs, SearchArgs


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
    assert {t["function"]["name"] for t in TOOLS} == {
        "aggregate_expenses", "list_expenses", "search_documents"}
    for t in TOOLS:
        assert "user_id" not in t["function"]["parameters"]["properties"]
