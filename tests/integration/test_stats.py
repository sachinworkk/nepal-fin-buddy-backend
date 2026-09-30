import datetime as dt

from app.core import dates


def bs_first_day(offset_months: int) -> dt.date:
    """AD date of day 1 of the BS month `offset_months` from the current one."""
    b = dates.ad_to_bs(dt.date.today())
    idx = b.year * 12 + b.month - 1 + offset_months
    return dates.bs_to_ad(idx // 12, idx % 12 + 1, 1)


async def add(client, auth, amount, day, category="groceries", merchant="Shop"):
    r = await client.post("/expenses", headers=auth, json={
        "amount": amount, "category": category, "merchant": merchant, "expense_date": day.isoformat()})
    assert r.status_code == 201, r.text


async def test_monthly_folds_days_into_bs_months(client, auth):
    first = bs_first_day(0)
    prev_last = first - dt.timedelta(days=1)  # last day of previous BS month
    await add(client, auth, "100.00", first)
    await add(client, auth, "50.00", first)
    await add(client, auth, "70.00", prev_last)
    r = await client.get("/stats/monthly", headers=auth, params={"months": 3})
    months = r.json()
    assert len(months) == 3
    assert months[-1]["total"] == "150.00" and months[-2]["total"] == "70.00"
    assert months[0]["total"] == "0.00"
    assert months[-1]["start"] == first.isoformat()
    assert months[-2]["end"] == prev_last.isoformat()
    assert months[-1]["bs_month"] == dates.ad_to_bs(dt.date.today()).month


async def test_by_category_shares(client, auth):
    d = bs_first_day(0)
    await add(client, auth, "300.00", d, "groceries")
    await add(client, auth, "100.00", d, "dining")
    r = (await client.get("/stats/by-category", headers=auth)).json()
    assert [c["category"] for c in r] == ["groceries", "dining"]
    assert r[0]["share"] == 0.75 and r[0]["count"] == 1
    empty = await client.get("/stats/by-category", headers=auth, params={"date_from": "2000-01-01", "date_to": "2000-01-02"})
    assert empty.json() == []


async def test_summary(client, auth):
    d = bs_first_day(0)
    await add(client, auth, "200.00", d, merchant="Bhatbhateni")
    await add(client, auth, "50.00", d, merchant="KFC")
    await add(client, auth, "40.00", d - dt.timedelta(days=1), merchant="KFC")
    s = (await client.get("/stats/summary", headers=auth)).json()
    assert s["this_month"] == "250.00" and s["last_month"] == "40.00"
    assert [m["merchant"] for m in s["top_merchants"]] == ["Bhatbhateni", "KFC"]


async def test_stats_are_per_user(client, auth, other_auth):
    await add(client, auth, "999.00", bs_first_day(0))
    s = (await client.get("/stats/summary", headers=other_auth)).json()
    assert s["this_month"] == "0.00" and s["top_merchants"] == []
    assert (await client.get("/stats/by-category", headers=other_auth)).json() == []
