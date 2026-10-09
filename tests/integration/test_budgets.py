import datetime as dt

from app.core import dates
from tests.integration.test_stats import add, bs_first_day


async def put(client, auth, category, amount):
    return await client.put(f"/budgets/{category}", headers=auth, json={"amount": amount})


async def test_upsert_updates_not_duplicates(client, auth):
    assert (await put(client, auth, "dining", "5000")).status_code == 200
    r = await put(client, auth, "dining", "7500.50")
    assert r.status_code == 200
    assert r.json()["amount"] == "7500.50" and r.json()["category"] == "dining"
    rows = (await client.get("/budgets", headers=auth)).json()
    assert [(b["category"], b["amount"]) for b in rows] == [("dining", "7500.50")]


async def test_status_ok_warning_over_this_bs_month_only(client, auth):
    d = bs_first_day(0)
    await put(client, auth, "groceries", "1000")  # 500 spent -> ok
    await put(client, auth, "dining", "1000")  # 850 spent -> warning
    await put(client, auth, "transport", "1000")  # 1200 spent -> over
    await put(client, auth, "rent", "1000")  # nothing this month, 900 last month -> ok, 0
    await add(client, auth, "500.00", d, "groceries")
    await add(client, auth, "850.00", d, "dining")
    await add(client, auth, "1200.00", d, "transport")
    await add(client, auth, "900.00", bs_first_day(0) - dt.timedelta(days=1), "rent")
    await add(client, auth, "999.00", d, "gifts")  # no budget: not listed

    rows = {b["category"]: b for b in (await client.get("/budgets", headers=auth)).json()}
    assert set(rows) == {"groceries", "dining", "transport", "rent"}
    assert (rows["groceries"]["spent"], rows["groceries"]["status"]) == ("500.00", "ok")
    assert rows["groceries"]["remaining"] == "500.00" and rows["groceries"]["percent"] == 50.0
    assert rows["dining"]["status"] == "warning" and rows["dining"]["percent"] == 85.0
    assert rows["transport"]["status"] == "over" and rows["transport"]["remaining"] == "-200.00"
    assert (rows["rent"]["spent"], rows["rent"]["status"]) == ("0.00", "ok")
    # most-used budget first
    ordered = [b["category"] for b in (await client.get("/budgets", headers=auth)).json()]
    assert ordered[:2] == ["transport", "dining"]


async def test_exactly_80_percent_warns_and_100_is_not_over(client, auth):
    d = bs_first_day(0)
    await put(client, auth, "dining", "1000")
    await add(client, auth, "800.00", d, "dining")
    await put(client, auth, "groceries", "1000")
    await add(client, auth, "1000.00", d, "groceries")
    rows = {b["category"]: b for b in (await client.get("/budgets", headers=auth)).json()}
    assert rows["dining"]["status"] == "warning"
    assert rows["groceries"]["status"] == "warning"  # at 100% is not yet over


async def test_budgets_are_per_user(client, auth, other_auth):
    await put(client, auth, "dining", "1000")
    await add(client, other_auth, "400.00", bs_first_day(0), "dining")
    assert (await client.get("/budgets", headers=other_auth)).json() == []
    assert (await client.delete("/budgets/dining", headers=other_auth)).status_code == 404
    mine = (await client.get("/budgets", headers=auth)).json()
    assert len(mine) == 1 and mine[0]["spent"] == "0.00"


async def test_delete(client, auth):
    await put(client, auth, "dining", "1000")
    assert (await client.delete("/budgets/dining", headers=auth)).status_code == 204
    assert (await client.get("/budgets", headers=auth)).json() == []
    assert (await client.delete("/budgets/dining", headers=auth)).status_code == 404


async def test_validation(client, auth):
    assert (await put(client, auth, "dining", "0")).status_code == 422
    assert (await put(client, auth, "dining", "-5")).status_code == 422
    assert (await put(client, auth, "dining", "10.123")).status_code == 422
    assert (await put(client, auth, "yachts", "100")).status_code == 422
    assert (await client.get("/budgets", headers=auth)).json() == []


async def test_requires_auth(client):
    assert (await client.get("/budgets")).status_code == 401


async def test_current_month_uses_bs_month(client, auth):
    """The current BS month is the window, even at its first and last day."""
    first, last = dates.month_bounds(*dates.today_bs())
    await put(client, auth, "dining", "1000")
    await add(client, auth, "100.00", first, "dining")
    await add(client, auth, "200.00", last, "dining")
    await add(client, auth, "400.00", last + dt.timedelta(days=1), "dining")  # next month
    [b] = (await client.get("/budgets", headers=auth)).json()
    assert b["spent"] == "300.00"
