

def exp(**kw):
    return {"amount": "250.50", "category": "groceries", "merchant": "Bhatbhateni",
            "expense_date": "2026-09-01", **kw}


async def test_crud(client, auth):
    r = await client.post("/expenses", headers=auth, json=exp())
    assert r.status_code == 201, r.text
    e = r.json()
    assert e["source"] == "manual" and e["expense_date_bs"] == "2083.05.16"
    eid = e["id"]
    r = await client.patch(f"/expenses/{eid}", headers=auth, json={"amount": "300.00", "category": "dining"})
    assert r.json()["amount"] == "300.00" and r.json()["category"] == "dining"
    assert (await client.get(f"/expenses/{eid}", headers=auth)).status_code == 200
    assert (await client.delete(f"/expenses/{eid}", headers=auth)).status_code == 204
    assert (await client.get(f"/expenses/{eid}", headers=auth)).status_code == 404


async def test_amount_normalised_to_two_places(client, auth):
    r = await client.post("/expenses", headers=auth, json=exp(amount="450"))
    assert r.json()["amount"] == "450.00"


async def test_bs_date_input(client, auth):
    r = await client.post("/expenses", headers=auth, json={
        "amount": "10", "category": "other", "expense_date_bs": "२०७४-०७-०६"})
    assert r.status_code == 201, r.text
    assert r.json()["expense_date_ad"] == "2017-10-23"
    assert r.json()["expense_date_bs"] == "2074.07.06"


async def test_validation(client, auth):
    for bad in (
        exp(amount="0"), exp(amount="-5"), exp(amount="1.234"), exp(category="crypto"),
        exp(expense_date_bs="2083.05.15"),  # both AD and BS
        {"amount": "5", "category": "other"},  # no date
        {"amount": "5", "category": "other", "expense_date_bs": "2074.07.40"},
    ):
        assert (await client.post("/expenses", headers=auth, json=bad)).status_code == 422, bad


async def test_list_filters_and_total(client, auth):
    for d, cat, m in [("2026-09-01", "groceries", "Bhatbhateni"), ("2026-09-05", "dining", "KFC"),
                      ("2026-08-01", "groceries", "BigMart")]:
        await client.post("/expenses", headers=auth, json=exp(expense_date=d, category=cat, merchant=m))
    r = await client.get("/expenses", headers=auth)
    assert r.headers["X-Total-Count"] == "3"
    assert [e["expense_date_ad"] for e in r.json()] == ["2026-09-05", "2026-09-01", "2026-08-01"]
    r = await client.get("/expenses", headers=auth, params={"category": "groceries", "date_from": "2026-09-01"})
    assert len(r.json()) == 1
    r = await client.get("/expenses", headers=auth, params={"q": "kfc"})
    assert len(r.json()) == 1
    r = await client.get("/expenses", headers=auth, params={"limit": 1, "offset": 1})
    assert len(r.json()) == 1 and r.headers["X-Total-Count"] == "3"
    assert (await client.get("/expenses", headers=auth, params={"limit": 500})).status_code == 422


async def test_user_isolation(client, auth, other_auth):
    eid = (await client.post("/expenses", headers=auth, json=exp())).json()["id"]
    assert (await client.get("/expenses", headers=other_auth)).json() == []
    for call in (client.get, client.delete):
        assert (await call(f"/expenses/{eid}", headers=other_auth)).status_code == 404
    assert (await client.patch(f"/expenses/{eid}", headers=other_auth, json={"note": "x"})).status_code == 404
    assert (await client.get(f"/expenses/{eid}", headers=auth)).status_code == 200
