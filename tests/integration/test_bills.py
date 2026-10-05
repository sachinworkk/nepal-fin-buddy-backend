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


async def test_flags_follow_current_values(client, auth, db):
    bid = await make_bill(client, auth, db)
    flags = (await client.get(f"/bills/{bid}", headers=auth)).json()["flags"]
    assert {"missing_total", "missing_date", "missing_pan"} <= set(flags)
    r = await client.patch(f"/bills/{bid}", headers=auth, json={
        "total_amount": "500.00", "bill_date_ad": "2026-09-01", "vendor_pan": "301234567"})
    assert r.json()["flags"] == []


async def test_confirm_with_bs_date(client, auth, db):
    bid = await make_bill(client, auth, db)
    body = {k: v for k, v in CONFIRM.items() if k != "bill_date_ad"}
    r = await client.post(f"/bills/{bid}/confirm", headers=auth,
                          json={**body, "bill_date_bs": "2083.05.10"})
    assert r.status_code == 200, r.text
    assert r.json()["bill_date_bs_raw"] == "2083.05.10"
    exp = (await client.get("/expenses", headers=auth)).json()[0]
    assert exp["expense_date_bs"] == "2083.05.10"
    assert exp["expense_date_ad"] == r.json()["bill_date_ad"]


async def test_confirm_needs_exactly_one_date(client, auth, db):
    bid = await make_bill(client, auth, db)
    body = {k: v for k, v in CONFIRM.items() if k != "bill_date_ad"}
    assert (await client.post(f"/bills/{bid}/confirm", headers=auth, json=body)).status_code == 422
    both = {**body, "bill_date_ad": "2026-09-01", "bill_date_bs": "2083.05.10"}
    assert (await client.post(f"/bills/{bid}/confirm", headers=auth, json=both)).status_code == 422


async def test_bill_image_owner_only(client, auth, other_auth, db, tmp_path):
    bid = await make_bill(client, auth, db)
    assert (await client.get(f"/bills/{bid}/image", headers=auth)).status_code == 404
    bill = await db.get(Bill, bid)
    img = tmp_path / "b.png"
    img.write_bytes(b"\x89PNG fake")
    bill.image_path, bill.mime_type = str(img), "image/png"
    await db.commit()
    r = await client.get(f"/bills/{bid}/image", headers=auth)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content == b"\x89PNG fake"
    assert (await client.get(f"/bills/{bid}", headers=auth)).json()["has_image"] is True
    assert (await client.get(f"/bills/{bid}/image", headers=other_auth)).status_code == 404
