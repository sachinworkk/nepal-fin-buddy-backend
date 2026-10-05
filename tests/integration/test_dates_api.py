async def test_convert_bs_to_ad(client, auth):
    r = await client.get("/dates/convert", headers=auth, params={"bs": "2074/7/6"})
    assert r.status_code == 200
    assert r.json() == {"ad": "2017-10-23", "bs": "2074.07.06", "bs_label": "6 Kartik 2074"}


async def test_convert_ad_to_bs(client, auth):
    r = await client.get("/dates/convert", headers=auth, params={"ad": "2017-10-23"})
    assert r.json()["bs"] == "2074.07.06"


async def test_convert_rejects_bad_input(client, auth):
    for params in ({}, {"bs": "2074.07.06", "ad": "2017-10-23"}, {"bs": "2074.07.40"},
                   {"bs": "nonsense"}):
        r = await client.get("/dates/convert", headers=auth, params=params)
        assert r.status_code == 422, params


async def test_convert_requires_auth(client):
    assert (await client.get("/dates/convert", params={"bs": "2074.07.06"})).status_code == 401
