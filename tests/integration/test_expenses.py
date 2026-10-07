

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
    await client.post("/expenses", headers=auth, json=exp(expense_date="2026-09-06", payment_method="qr"))
    r = await client.get("/expenses", headers=auth, params={"payment_method": "qr"})
    assert len(r.json()) == 1 and r.headers["X-Total-Count"] == "1"
    assert (await client.get("/expenses", headers=auth, params={"payment_method": "bitcoin"})).status_code == 422


async def test_user_isolation(client, auth, other_auth):
    eid = (await client.post("/expenses", headers=auth, json=exp())).json()["id"]
    assert (await client.get("/expenses", headers=other_auth)).json() == []
    for call in (client.get, client.delete):
        assert (await call(f"/expenses/{eid}", headers=other_auth)).status_code == 404
    assert (await client.patch(f"/expenses/{eid}", headers=other_auth, json={"note": "x"})).status_code == 404
    assert (await client.get(f"/expenses/{eid}", headers=auth)).status_code == 200


BILL = {"vendor_pan": "301234567", "bill_number": "INV-42", "subtotal": "1000.00",
        "discount": "0.00", "taxable_amount": "1000.00", "vat_amount": "130.00"}


async def test_manual_bill_details(client, auth):
    r = await client.post("/expenses", headers=auth, json=exp(
        amount="1130.00", payment_method="cash", bill=BILL))
    assert r.status_code == 201, r.text
    e = r.json()
    assert e["source"] == "manual" and e["bill_id"]
    assert e["bill"] == {**BILL, "vendor_name": "Bhatbhateni", "total_amount": "1130.00"}
    # The image-less bill is confirmed, so it lists alongside snapped bills.
    bill = (await client.get(f"/bills/{e['bill_id']}", headers=auth)).json()
    assert bill["status"] == "confirmed" and not bill["has_image"]
    assert bill["bill_date_ad"] == "2026-09-01" and bill["payment_method"] == "cash"

    # Editing the expense keeps the bill in step.
    r = await client.patch(f"/expenses/{e['id']}", headers=auth, json={
        "amount": "1200.00", "merchant": "BigMart", "bill": {**BILL, "bill_number": "INV-43"}})
    assert r.json()["bill"]["bill_number"] == "INV-43"
    assert r.json()["bill"]["vendor_name"] == "BigMart" and r.json()["bill"]["total_amount"] == "1200.00"

    # An empty bill removes it; deleting the expense removes a remaining bill too.
    r = await client.patch(f"/expenses/{e['id']}", headers=auth, json={"bill": {}})
    assert r.json()["bill"] is None and r.json()["bill_id"] is None
    assert (await client.get(f"/bills/{e['bill_id']}", headers=auth)).status_code == 404
    r = await client.post("/expenses", headers=auth, json=exp(bill=BILL))
    await client.delete(f"/expenses/{r.json()['id']}", headers=auth)
    assert (await client.get("/bills", headers=auth)).json() == []


async def test_no_bill_without_details(client, auth):
    r = await client.post("/expenses", headers=auth, json=exp(bill={"vendor_pan": ""}))
    assert r.json()["bill"] is None and r.json()["bill_id"] is None
    assert (await client.post("/expenses", headers=auth, json=exp(
        bill={**BILL, "vat_amount": "1.234"}))).status_code == 422
