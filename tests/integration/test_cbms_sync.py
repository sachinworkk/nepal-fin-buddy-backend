"""API -> queue -> worker -> mock CBMS, covering every CBMS response code."""

import datetime as dt

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.cbms import messages
from app.config import get_settings
from app.models import CbmsSyncQueue
from app.worker.sync import run_once
from mock_cbms.app import create_app
from tests.integration.test_invoices import credit_note, inv

SANDBOX = {"username": "Test_CBMS", "password": "test@321", "seller_pan": "999999999"}


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(get_settings(), "cbms_backoff_base_seconds", 0)


@pytest_asyncio.fixture
async def cbms():
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://mock-cbms") as c:
        yield c


@pytest.fixture
def maker(app_engine):
    return async_sessionmaker(app_engine, expire_on_commit=False)


async def creds(client, auth, **kw):
    r = await client.put("/settings/ird-credentials", headers=auth, json={**SANDBOX, **kw})
    assert r.status_code == 200


async def create(client, auth, body) -> str:
    r = await client.post("/invoices", headers=auth, json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def sync_of(client, auth, iid) -> dict:
    return (await client.get(f"/invoices/{iid}", headers=auth)).json()["sync"]


async def test_200_synced(client, auth, cbms, maker):
    await creds(client, auth)
    iid = await create(client, auth, inv())
    assert await run_once(maker, cbms) == 1
    s = await sync_of(client, auth, iid)
    assert s["status"] == "synced" and s["last_response_code"] == 200
    assert s["attempts"] == 1 and s["last_error"] is None and s["synced_at"]
    assert await run_once(maker, cbms) == 0


async def test_100_bad_credentials_then_resync(client, auth, cbms, maker):
    await creds(client, auth, password="wrong")
    iid = await create(client, auth, inv())
    await run_once(maker, cbms)
    s = await sync_of(client, auth, iid)
    assert s["status"] == "failed" and s["last_response_code"] == 100
    assert s["last_error"] == messages.CODES[100].en and s["last_error_ne"] == messages.CODES[100].ne
    assert await run_once(maker, cbms) == 0  # no automatic retry

    await creds(client, auth)
    assert (await client.post(f"/invoices/{iid}/resync", headers=auth)).status_code == 200
    await run_once(maker, cbms)
    assert (await sync_of(client, auth, iid))["status"] == "synced"


async def test_101_resubmission_counts_as_synced(client, auth, cbms, maker, db):
    await creds(client, auth)
    iid = await create(client, auth, inv())
    await run_once(maker, cbms)
    # Simulate a worker that submitted but died before recording the result,
    # and whose lease has since expired.
    stale = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    await db.execute(update(CbmsSyncQueue).values(
        status="in_progress", synced_at=None, next_attempt_at=stale))
    await db.commit()
    await run_once(maker, cbms)
    s = await sync_of(client, auth, iid)
    assert s["status"] == "synced" and s["last_response_code"] == 101


async def test_102_invalid_payload(client, auth, cbms, maker):
    await creds(client, auth, seller_pan="12345")  # CBMS wants a 9-digit PAN
    iid = await create(client, auth, inv())
    await run_once(maker, cbms)
    s = await sync_of(client, auth, iid)
    assert s["status"] == "failed" and s["last_response_code"] == 102


async def test_103_retries_with_backoff_then_gives_up(client, auth, cbms, maker):
    await creds(client, auth)
    iid = await create(client, auth, inv(buyer_name="FORCE_103"))
    await run_once(maker, cbms)
    s = await sync_of(client, auth, iid)
    assert s["status"] == "pending" and s["attempts"] == 1 and s["last_response_code"] == 103
    assert s["last_error"] == messages.CODES[103].en
    for _ in range(5):
        await run_once(maker, cbms)
    s = await sync_of(client, auth, iid)
    assert s["status"] == "failed" and s["attempts"] == 6
    assert s["last_error"] == messages.GAVE_UP.en
    assert await run_once(maker, cbms) == 0


async def test_103_transient_recovers(client, auth, cbms, maker):
    await creds(client, auth)
    iid = await create(client, auth, inv(buyer_name="FLAKY_103"))
    await run_once(maker, cbms)
    assert (await sync_of(client, auth, iid))["status"] == "pending"
    await run_once(maker, cbms)
    s = await sync_of(client, auth, iid)
    assert s["status"] == "synced" and s["attempts"] == 2 and s["last_error"] is None


async def test_104_model_invalid(client, auth, cbms, maker):
    await creds(client, auth)
    iid = await create(client, auth, inv(buyer_name="FORCE_104"))
    await run_once(maker, cbms)
    s = await sync_of(client, auth, iid)
    assert s["status"] == "failed" and s["last_response_code"] == 104


async def test_backoff_delays_next_attempt(client, auth, cbms, maker, monkeypatch):
    monkeypatch.setattr(get_settings(), "cbms_backoff_base_seconds", 30)
    await creds(client, auth)
    await create(client, auth, inv(buyer_name="FORCE_103"))
    assert await run_once(maker, cbms) == 1
    assert await run_once(maker, cbms) == 0  # not due for another 15-30s


async def test_unreachable_retries(client, auth, maker):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    await creds(client, auth)
    iid = await create(client, auth, inv())
    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse),
                                 base_url="http://mock-cbms") as down:
        await run_once(maker, down)
    s = await sync_of(client, auth, iid)
    assert s["status"] == "pending" and s["last_response_code"] is None
    assert s["last_error"] == messages.UNREACHABLE.en


async def test_no_credentials_fails_without_submitting(client, auth, maker):
    def unexpected(request):
        raise AssertionError("should not call CBMS")

    iid = await create(client, auth, inv())
    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected),
                                 base_url="http://mock-cbms") as c:
        await run_once(maker, c)
    s = await sync_of(client, auth, iid)
    assert s["status"] == "failed" and s["last_error"] == messages.NO_CREDENTIALS.en


async def test_credit_note_waits_for_original(client, auth, cbms, maker):
    await creds(client, auth)
    bill = await create(client, auth, inv(buyer_name="FLAKY_103"))
    cn = await create(client, auth, credit_note())
    await run_once(maker, cbms)
    assert (await sync_of(client, auth, bill))["status"] == "pending"
    s = await sync_of(client, auth, cn)
    assert s["status"] == "pending" and s["attempts"] == 0
    assert s["last_error"] == messages.WAITING_FOR_ORIGINAL.en
    await run_once(maker, cbms)
    assert (await sync_of(client, auth, bill))["status"] == "synced"
    s = await sync_of(client, auth, cn)
    assert s["status"] == "synced" and s["attempts"] == 1 and s["last_response_code"] == 200


async def test_expired_lease_is_reclaimed(client, auth, cbms, maker, db):
    await creds(client, auth)
    iid = await create(client, auth, inv())
    stale = dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)
    await db.execute(update(CbmsSyncQueue).values(
        status="in_progress", attempts=1, next_attempt_at=stale))
    await db.commit()
    await run_once(maker, cbms)
    s = await sync_of(client, auth, iid)
    assert s["status"] == "synced" and s["attempts"] == 2


async def test_105_credit_note_original_unknown_to_cbms(client, auth, cbms, maker):
    await creds(client, auth)
    await create(client, auth, inv())
    await run_once(maker, cbms)  # original synced on this mock
    cn = await create(client, auth, credit_note())
    fresh = httpx.ASGITransport(app=create_app())  # a CBMS that never saw the original
    async with httpx.AsyncClient(transport=fresh, base_url="http://mock-cbms") as other:
        await run_once(maker, other)
    s = await sync_of(client, auth, cn)
    assert s["status"] == "pending" and s["last_response_code"] == 105
    assert s["last_error"] == messages.CODES[105].en
