async def test_put_get_delete_never_returns_password(client, auth):
    assert (await client.get("/settings/ird-credentials", headers=auth)).status_code == 404
    body = {"username": "sandbox-user", "password": "sandbox-pass", "seller_pan": "123456789"}
    r = await client.put("/settings/ird-credentials", headers=auth, json=body)
    assert r.status_code == 200
    assert r.json()["username"] == "sandbox-user" and r.json()["has_password"] is True
    assert "sandbox-pass" not in r.text and "password" not in r.json()
    r = await client.put("/settings/ird-credentials", headers=auth, json={**body, "seller_pan": "987654321"})
    assert r.json()["seller_pan"] == "987654321"
    assert (await client.delete("/settings/ird-credentials", headers=auth)).status_code == 204
    assert (await client.get("/settings/ird-credentials", headers=auth)).status_code == 404


async def test_credentials_stored_encrypted_and_isolated(client, auth, other_auth, db):
    from sqlalchemy import select

    from app.core.crypto import decrypt
    from app.models import IrdCredential

    await client.put("/settings/ird-credentials", headers=auth,
                     json={"username": "u1", "password": "p1", "seller_pan": "1"})
    row = (await db.scalars(select(IrdCredential))).one()
    assert row.username_enc != "u1" and row.password_enc != "p1"
    assert decrypt(row.password_enc) == "p1"
    assert (await client.get("/settings/ird-credentials", headers=other_auth)).status_code == 404
