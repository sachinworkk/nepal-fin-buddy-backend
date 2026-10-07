from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.categories import Category as CategoryEnum
from app.core.payment_methods import PaymentMethod as PaymentMethodEnum
from app.models import Category, PaymentMethod


async def test_enums_match_seeded_tables(app_engine):
    async with async_sessionmaker(app_engine)() as s:
        slugs = set(await s.scalars(select(Category.slug)))
        codes = set(await s.scalars(select(PaymentMethod.code)))
    assert slugs == {c.value for c in CategoryEnum}
    assert codes == {m.value for m in PaymentMethodEnum}


async def test_lookup_endpoints(client, auth):
    cats = (await client.get("/categories", headers=auth)).json()
    assert [c["slug"] for c in cats][0] == "groceries" and len(cats) == len(CategoryEnum)
    assert all(c["label_en"] and c["label_ne"] for c in cats)
    pms = (await client.get("/payment-methods", headers=auth)).json()
    assert [p["code"] for p in pms] == ["cash", "card", "qr", "cheque"]
    assert (await client.get("/categories")).status_code == 401


async def test_expense_payment_method_validated(client, auth):
    body = {"amount": "10", "category": "other", "expense_date": "2026-09-01"}
    r = await client.post("/expenses", headers=auth, json={**body, "payment_method": "card"})
    assert r.status_code == 201 and r.json()["payment_method"] == "card"
    r = await client.post("/expenses", headers=auth, json={**body, "payment_method": "bitcoin"})
    assert r.status_code == 422
