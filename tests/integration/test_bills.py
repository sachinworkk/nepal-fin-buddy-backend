
from app.models import Bill, BillItem


async def make_bill(client, auth, db) -> str:
    """Bills are created by the extractor (step 5); insert one directly."""
    me = (await client.get("/auth/me", headers=auth)).json()
    bill = Bill(user_id=me["id"], vendor_name="Bhatbhateni", status="extracted",
                items=[BillItem(description="Rice", amount="500.00")])
    db.add(bill)
    await db.commit()
    return str(bill.id)


CONFIRM = {"category": "groceries", "total_amount": "1130.00", "bill_date_ad": "2026-09-01",
           "vat_amount": "130.00", "items": [{"description": "Rice", "amount": "1000.00"}]}


async def test_get_update_and_list(client, auth, db):
    bid = await make_bill(client, auth, db)
    r = await client.get(f"/bills/{bid}", headers=auth)
    assert r.json()["items"][0]["description"] == "Rice"
    r = await client.patch(f"/bills/{bid}", headers=auth, json={
        "vendor_name": "BigMart", "items": [{"description": "Oil", "amount": "300.00"}]})
    assert r.json()["vendor_name"] == "BigMart"
    assert [i["description"] for i in r.json()["items"]] == ["Oil"]
    assert len((await client.get("/bills", headers=auth, params={"status_": "extracted"})).json()) == 1
    assert (await client.get("/bills", headers=auth, params={"status_": "confirmed"})).json() == []


async def test_confirm_creates_expense_once(client, auth, db):
    bid = await make_bill(client, auth, db)
    r = await client.post(f"/bills/{bid}/confirm", headers=auth, json=CONFIRM)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed"
    exps = (await client.get("/expenses", headers=auth)).json()
    assert len(exps) == 1
    assert exps[0]["source"] == "bill" and exps[0]["bill_id"] == bid
    assert exps[0]["amount"] == "1130.00" and exps[0]["merchant"] == "Bhatbhateni"
    assert (await client.post(f"/bills/{bid}/confirm", headers=auth, json=CONFIRM)).status_code == 409
    assert (await client.patch(f"/bills/{bid}", headers=auth, json={"vendor_name": "x"})).status_code == 409


async def test_confirm_validation(client, auth, db):
    bid = await make_bill(client, auth, db)
    bad = {**CONFIRM, "total_amount": "0"}
    assert (await client.post(f"/bills/{bid}/confirm", headers=auth, json=bad)).status_code == 422


async def test_bill_isolation_and_delete(client, auth, other_auth, db):
    bid = await make_bill(client, auth, db)
    assert (await client.get(f"/bills/{bid}", headers=other_auth)).status_code == 404
    assert (await client.delete(f"/bills/{bid}", headers=other_auth)).status_code == 404
    assert (await client.get("/bills", headers=other_auth)).json() == []
    assert (await client.delete(f"/bills/{bid}", headers=auth)).status_code == 204
    assert (await client.get(f"/bills/{bid}", headers=auth)).status_code == 404
