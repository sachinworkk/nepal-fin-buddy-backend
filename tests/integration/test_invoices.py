from sqlalchemy import update

from app.models import CbmsSyncQueue


def inv(**kw):
    return {"invoice_number": "INV-1", "invoice_date_bs": "2081.05.10", "buyer_name": "Ram Traders",
            "total_sales": "1130.00", "taxable_sales_vat": "1000.00", "vat": "130.00", **kw}


def credit_note(**kw):
    return inv(invoice_number="CN-1", is_return=True, ref_invoice_number="INV-1",
               credit_note_number="CN-1", credit_note_date_bs="2081.05.20",
               reason_for_return="Damaged goods", **kw)


async def test_create_derives_dates_and_enqueues(client, auth):
    r = await client.post("/invoices", headers=auth, json=inv())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["fiscal_year"] == "2081.082" and body["invoice_date_ad"] == "2024-08-26"
    assert body["sync"]["status"] == "pending" and body["sync"]["attempts"] == 0
    got = (await client.get(f"/invoices/{body['id']}", headers=auth)).json()
    assert got["sync"]["status"] == "pending"
    assert len((await client.get("/invoices", headers=auth, params={"fiscal_year": "2080.081"})).json()) == 0


async def test_duplicate_invoice_409(client, auth):
    assert (await client.post("/invoices", headers=auth, json=inv())).status_code == 201
    assert (await client.post("/invoices", headers=auth, json=inv())).status_code == 409


async def test_validation(client, auth):
    for bad in (inv(total_sales="0"), inv(vat="-1"), inv(invoice_date_bs="2081.05.40")):
        assert (await client.post("/invoices", headers=auth, json=bad)).status_code == 422, bad
    missing = credit_note()
    del missing["reason_for_return"]
    assert (await client.post("/invoices", headers=auth, json=missing)).status_code == 422


async def test_credit_note_requires_existing_reference(client, auth):
    assert (await client.post("/invoices", headers=auth, json=credit_note())).status_code == 422
    await client.post("/invoices", headers=auth, json=inv())
    r = await client.post("/invoices", headers=auth, json=credit_note())
    assert r.status_code == 201, r.text
    assert r.json()["is_return"] is True
    assert r.json()["sync"]["status"] == "pending"


async def test_credit_note_cannot_reference_other_users_invoice(client, auth, other_auth):
    await client.post("/invoices", headers=auth, json=inv())
    assert (await client.post("/invoices", headers=other_auth, json=credit_note())).status_code == 422


async def test_resync_only_from_failed(client, auth, db):
    iid = (await client.post("/invoices", headers=auth, json=inv())).json()["id"]
    assert (await client.post(f"/invoices/{iid}/resync", headers=auth)).status_code == 409
    await db.execute(update(CbmsSyncQueue).values(
        status="failed", attempts=6, last_error="Invalid credentials"))
    await db.commit()
    r = await client.post(f"/invoices/{iid}/resync", headers=auth)
    assert r.status_code == 200
    assert r.json()["sync"] == {**r.json()["sync"], "status": "pending", "attempts": 0, "last_error": None}


async def test_no_update_or_delete_and_isolation(client, auth, other_auth):
    iid = (await client.post("/invoices", headers=auth, json=inv())).json()["id"]
    assert (await client.delete(f"/invoices/{iid}", headers=auth)).status_code == 405
    assert (await client.patch(f"/invoices/{iid}", headers=auth, json={})).status_code == 405
    assert (await client.get(f"/invoices/{iid}", headers=other_auth)).status_code == 404
    assert (await client.post(f"/invoices/{iid}/resync", headers=other_auth)).status_code == 404
    assert (await client.get("/invoices", headers=other_auth)).json() == []
    # the same invoice number is fine for a different user
    assert (await client.post("/invoices", headers=other_auth, json=inv())).status_code == 201
