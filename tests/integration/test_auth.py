async def test_register_login_me(client):
    r = await client.post("/auth/register", json={"email": "A@Example.com", "password": "password123"})
    assert r.status_code == 201
    r = await client.post("/auth/login", json={"email": "a@example.com", "password": "password123"})
    assert r.status_code == 200
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = (await client.get("/auth/me", headers=h)).json()
    assert me["email"] == "a@example.com" and me["preferred_language"] == "en"
    r = await client.patch("/auth/me", headers=h, json={"preferred_language": "ne", "full_name": "Ram"})
    assert r.json()["preferred_language"] == "ne" and r.json()["full_name"] == "Ram"


async def test_duplicate_email_and_bad_login(client):
    body = {"email": "d@example.com", "password": "password123"}
    assert (await client.post("/auth/register", json=body)).status_code == 201
    assert (await client.post("/auth/register", json=body)).status_code == 409
    bad = await client.post("/auth/login", json={**body, "password": "nope-nope"})
    assert bad.status_code == 401
    unknown = await client.post("/auth/login", json={"email": "x@example.com", "password": "password123"})
    assert unknown.status_code == 401


async def test_missing_or_garbage_token(client):
    assert (await client.get("/auth/me")).status_code == 401
    r = await client.get("/auth/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401


async def test_weak_password_rejected(client):
    r = await client.post("/auth/register", json={"email": "w@example.com", "password": "short"})
    assert r.status_code == 422
