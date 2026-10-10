"""Customer cash-bill submission to the IRD prize lottery, against mock_prize."""

import datetime as dt
import time
import uuid
from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.api.prize import get_prize_client
from app.config import get_settings
from app.core.security import decode_access_token
from app.models import Bill, PrizeEnrollment
from app.services import prize as prize_service
from mock_prize.app import create_app


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    monkeypatch.setattr(get_settings(), "prize_user_submit_interval_seconds", 0)


@pytest.fixture(autouse=True)
def _fresh_portal_caches():
    prize_service.reset_portal_caches()


@pytest_asyncio.fixture
async def portal(client):
    """Override the app's prize HTTP client with an in-process mock portal."""
    mock = create_app()
    http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=mock), base_url="http://mock-prize/api/v1"
    )

    async def override():
        yield http

    from app.main import app as fastapi_app

    fastapi_app.dependency_overrides[get_prize_client] = override
    yield http
    fastapi_app.dependency_overrides.pop(get_prize_client, None)
    await http.aclose()


def _started(seconds_ago: float = 30) -> int:
    return int((time.time() - seconds_ago) * 1000)


def body(bill_id: str, **kw) -> dict:
    base = {
        "bill_id": bill_id,
        "payment_method": "Cash", "name": "Ram Bahadur", "address": "Kathmandu",
        "mobile_number": "9801234567", "turnstile_token": "TEST-token",
        "form_started_at": _started(),
    }
    return {**base, **kw}


async def _user_id(auth) -> uuid.UUID:
    return decode_access_token(auth["Authorization"].split()[1])


async def _confirmed_bill(db, user, **kw) -> Bill:
    fields = {
        "vendor_pan": "123456789", "bill_number": "BILL-9",
        "bill_date_ad": dt.date(2026, 10, 1), "total_amount": Decimal("2500"),
        "status": "confirmed",
    }
    fields.update(kw)
    bill = Bill(user_id=user, **fields)
    db.add(bill)
    await db.commit()
    await db.refresh(bill)
    return bill


async def test_config_exposes_sitekey(client):
    r = await client.get("/prize/config")
    assert r.status_code == 200
    out = r.json()
    assert out["turnstile_sitekey"] == get_settings().prize_turnstile_sitekey
    assert out["payment_methods"] == ["Cash"]


async def test_fiscal_years_proxy(client, portal, auth):
    r = await client.get("/prize/fiscal-years", headers=auth)
    assert r.status_code == 200
    assert r.json()["fiscal_years"][0]["fiscal_year_code"] == "2083-84"


async def test_submit_success(client, portal, auth, db):
    bill = await _confirmed_bill(db, await _user_id(auth))
    r = await client.post("/prize/enrollments", headers=auth, json=body(str(bill.id)))
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["status"] == "submitted"
    assert out["bill_id"] == str(bill.id)
    assert out["seller_pan_no"] == "123456789"
    assert out["bill_number"] == "BILL-9"
    assert out["billed_total_amount"] == "2500.00"
    assert len(out["prize_coupon_number"]) == 12
    assert out["verification_id"].startswith("VID-")
    assert out["duplicate"] is False
    row = (await db.scalars(select(PrizeEnrollment))).one()
    assert row.status == "submitted" and row.bill_id == bill.id
    assert row.prize_coupon_number == out["prize_coupon_number"]


async def test_missing_bill_id_rejected(client, portal, auth):
    payload = body(str(uuid.uuid4()))
    del payload["bill_id"]
    r = await client.post("/prize/enrollments", headers=auth, json=payload)
    assert r.status_code == 422


async def test_duplicate_bill_rejected(client, portal, auth, db):
    bill = await _confirmed_bill(db, await _user_id(auth))
    assert (await client.post("/prize/enrollments", headers=auth,
                              json=body(str(bill.id)))).status_code == 201
    r = await client.post("/prize/enrollments", headers=auth, json=body(str(bill.id)))
    assert r.status_code == 409


async def test_local_validation_errors(client, portal, auth, db):
    user = await _user_id(auth)
    bad_pan = await _confirmed_bill(db, user, vendor_pan="12345")
    r = await client.post("/prize/enrollments", headers=auth, json=body(str(bad_pan.id)))
    assert r.status_code == 422
    assert "seller_pan_no" in r.json()["detail"]

    zero = await _confirmed_bill(db, user, total_amount=Decimal("0"))
    r = await client.post("/prize/enrollments", headers=auth, json=body(str(zero.id)))
    assert r.status_code == 422
    assert "billed_total_amount" in r.json()["detail"]

    good = await _confirmed_bill(db, user)
    r = await client.post("/prize/enrollments", headers=auth,
                          json=body(str(good.id), mobile_number="12345"))
    assert r.status_code == 422
    r = await client.post("/prize/enrollments", headers=auth,
                          json=body(str(good.id), name="R"))
    assert r.status_code == 422
    r = await client.post("/prize/enrollments", headers=auth,
                          json=body(str(good.id), payment_method="Card"))
    assert r.status_code == 422


async def test_form_started_at_window(client, portal, auth, db):
    bill = await _confirmed_bill(db, await _user_id(auth))
    r = await client.post("/prize/enrollments", headers=auth,
                          json=body(str(bill.id), form_started_at=_started(seconds_ago=0)))
    assert r.status_code == 422
    r = await client.post("/prize/enrollments", headers=auth, json=body(
        str(bill.id), form_started_at=_started(seconds_ago=100000)))
    assert r.status_code == 422


async def test_turnstile_siteverify_failure(client, portal, auth, db, monkeypatch):
    monkeypatch.setattr(
        get_settings(), "prize_turnstile_secret_key",
        "2x0000000000000000000000000000000AA",
    )
    bill = await _confirmed_bill(db, await _user_id(auth))
    r = await client.post("/prize/enrollments", headers=auth, json=body(str(bill.id)))
    assert r.status_code == 400
    assert "security verification" in r.json()["detail"]
    assert (await db.scalars(select(PrizeEnrollment))).first() is None


async def test_portal_turnstile_failure_passthrough(client, portal, auth, db):
    bill = await _confirmed_bill(db, await _user_id(auth))
    r = await client.post("/prize/enrollments", headers=auth,
                          json=body(str(bill.id), turnstile_token="FORCE_TURNSTILE_FAIL"))
    assert r.status_code == 400
    assert "security verification" in r.json()["detail"]
    row = (await db.scalars(select(PrizeEnrollment))).one()
    assert row.status == "failed" and "security verification" in row.last_error


async def test_unconfirmed_bill_rejected(client, portal, auth, db):
    user = await _user_id(auth)
    bill = Bill(user_id=user, vendor_pan="123456789", bill_number="BILL-2",
                bill_date_ad=dt.date(2026, 10, 1), total_amount=Decimal("100"),
                status="extracted")
    db.add(bill)
    await db.commit()
    r = await client.post("/prize/enrollments", headers=auth, json=body(str(bill.id)))
    assert r.status_code == 409


async def test_bill_prefill_missing_fields(client, portal, auth, db):
    bill = await _confirmed_bill(db, await _user_id(auth), vendor_pan=None,
                                 bill_number=None, bill_date_ad=None, total_amount=None)
    r = await client.post("/prize/enrollments", headers=auth, json=body(str(bill.id)))
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert set(detail) == {"bill_number", "seller_pan_no", "bill_date", "billed_total_amount"}


async def test_throttle(client, portal, auth, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "prize_user_submit_interval_seconds", 8)
    user = await _user_id(auth)
    first_bill = await _confirmed_bill(db, user)
    second_bill = await _confirmed_bill(db, user, bill_number="BILL-10")
    first = await client.post("/prize/enrollments", headers=auth, json=body(str(first_bill.id)))
    assert first.status_code == 201
    second = await client.post("/prize/enrollments", headers=auth,
                               json=body(str(second_bill.id)))
    assert second.status_code == 429


async def test_list_and_get_isolated_per_user(client, portal, auth, other_auth, db):
    bill = await _confirmed_bill(db, await _user_id(auth))
    await client.post("/prize/enrollments", headers=auth, json=body(str(bill.id)))
    r = await client.get("/prize/enrollments", headers=auth)
    rows = r.json()
    assert len(rows) == 1
    eid = rows[0]["id"]
    assert (await client.get(f"/prize/enrollments/{eid}", headers=auth)).status_code == 200
    assert (await client.get(f"/prize/enrollments/{eid}", headers=other_auth)
            ).status_code == 404
    assert (await client.get("/prize/enrollments", headers=other_auth)).json() == []


async def test_list_enrollments_filters_by_bill_id(client, portal, auth, db):
    user = await _user_id(auth)
    bill_a = await _confirmed_bill(db, user)
    bill_b = await _confirmed_bill(db, user, bill_number="BILL-20")
    await client.post("/prize/enrollments", headers=auth, json=body(str(bill_a.id)))

    r = await client.get(f"/prize/enrollments?bill_id={bill_a.id}", headers=auth)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1 and rows[0]["bill_id"] == str(bill_a.id)

    assert (await client.get(f"/prize/enrollments?bill_id={bill_b.id}",
                             headers=auth)).json() == []


async def test_bill_prize_enrolled_flag(client, portal, auth, other_auth, db):
    user = await _user_id(auth)
    bill = await _confirmed_bill(db, user)
    r = await client.get(f"/bills/{bill.id}", headers=auth)
    assert r.status_code == 200
    assert r.json()["prize_enrolled"] is False

    assert (await client.post("/prize/enrollments", headers=auth,
                              json=body(str(bill.id)))).status_code == 201

    detail = (await client.get(f"/bills/{bill.id}", headers=auth)).json()
    assert detail["prize_enrolled"] is True
    listed = (await client.get("/bills", headers=auth)).json()
    assert [b["prize_enrolled"] for b in listed if b["id"] == str(bill.id)] == [True]

    # Failed attempts also count as enrolled — the bill must not be silently resubmitted.
    failed = await _confirmed_bill(db, user, bill_number="BILL-21")
    r = await client.post("/prize/enrollments", headers=auth, json=body(
        str(failed.id), turnstile_token="FORCE_TURNSTILE_FAIL"))
    assert r.status_code == 400
    assert (await client.get(f"/bills/{failed.id}", headers=auth)
            ).json()["prize_enrolled"] is True

    # Other users never see this bill, and their own bills are not enrolled.
    assert (await client.get(f"/bills/{bill.id}", headers=other_auth)).status_code == 404
    other_bill = await _confirmed_bill(db, await _user_id(other_auth),
                                       bill_number="BILL-22")
    assert (await client.get(f"/bills/{other_bill.id}", headers=other_auth)
            ).json()["prize_enrolled"] is False


def handoff_body(bill_id: str, **kw) -> dict:
    base = {"bill_id": bill_id, "name": "Ram Bahadur", "address": "Kathmandu",
            "mobile_number": "9801234567"}
    return {**base, **kw}


async def test_eligible_lists_only_valid_unenrolled_bills(client, portal, auth, db):
    user = await _user_id(auth)
    good = await _confirmed_bill(db, user)
    await _confirmed_bill(db, user, bill_number="BILL-BADPAN", vendor_pan="12345")
    await _confirmed_bill(db, user, bill_number="BILL-NOAMT", total_amount=None)
    await _confirmed_bill(db, user, bill_number="BILL-UNCONF", status="extracted")
    taken = await _confirmed_bill(db, user, bill_number="BILL-TAKEN")
    assert (await client.post("/prize/enrollments/handoff", headers=auth,
                              json=handoff_body(str(taken.id)))).status_code == 200

    r = await client.get("/prize/eligible", headers=auth)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert [x["bill_id"] for x in rows] == [str(good.id)]
    assert rows[0]["fiscal_year_code"] == "2083-84"
    assert rows[0]["fiscal_year_status"] == "ACTIVE"
    assert rows[0]["days_left"] >= 0


async def test_eligible_isolated_per_user(client, portal, auth, other_auth, db):
    await _confirmed_bill(db, await _user_id(auth))
    assert (await client.get("/prize/eligible", headers=other_auth)).json() == []


async def test_handoff_creates_row_and_is_idempotent(client, portal, auth, db):
    bill = await _confirmed_bill(db, await _user_id(auth))
    r = await client.post("/prize/enrollments/handoff", headers=auth,
                          json=handoff_body(str(bill.id)))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["portal_url"] == get_settings().prize_portal_url
    assert out["fields"]["seller_pan_no"] == "123456789"
    assert out["fields"]["billed_total_amount"] == "2500.00"
    assert out["fields"]["mobile_number"] == "9801234567"

    again = await client.post("/prize/enrollments/handoff", headers=auth,
                              json=handoff_body(str(bill.id), address="Lalitpur"))
    assert again.json()["enrollment_id"] == out["enrollment_id"]
    row = (await db.scalars(select(PrizeEnrollment))).one()
    assert row.status == "handed_off" and row.address == "Lalitpur"
    assert (await client.get(f"/bills/{bill.id}", headers=auth)).json()["prize_enrolled"] is True


async def test_handoff_validation(client, portal, auth, db):
    user = await _user_id(auth)
    good = await _confirmed_bill(db, user)
    r = await client.post("/prize/enrollments/handoff", headers=auth,
                          json=handoff_body(str(good.id), mobile_number="1234567890"))
    assert r.status_code == 422 and "mobile_number" in r.json()["detail"]
    unconfirmed = await _confirmed_bill(db, user, bill_number="B-2", status="extracted")
    r = await client.post("/prize/enrollments/handoff", headers=auth,
                          json=handoff_body(str(unconfirmed.id)))
    assert r.status_code == 409


async def test_mark_submitted_flow(client, portal, auth, other_auth, db):
    bill = await _confirmed_bill(db, await _user_id(auth))
    eid = (await client.post("/prize/enrollments/handoff", headers=auth,
                             json=handoff_body(str(bill.id)))).json()["enrollment_id"]

    assert (await client.patch(f"/prize/enrollments/{eid}", headers=other_auth,
                               json={"status": "submitted"})).status_code == 404
    r = await client.patch(f"/prize/enrollments/{eid}", headers=auth,
                           json={"status": "submitted", "prize_coupon_number": "ABC123456789"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "submitted"
    assert r.json()["prize_coupon_number"] == "ABC123456789"

    # Already submitted: no re-marking, no new hand-off, and it leaves the eligible list.
    assert (await client.patch(f"/prize/enrollments/{eid}", headers=auth,
                               json={"status": "submitted"})).status_code == 409
    assert (await client.post("/prize/enrollments/handoff", headers=auth,
                              json=handoff_body(str(bill.id)))).status_code == 409
    assert (await client.get("/prize/eligible", headers=auth)).json() == []
    assert (await client.patch(f"/prize/enrollments/{eid}", headers=auth,
                               json={"status": "dismissed"})).status_code == 409
    assert (await client.patch(f"/prize/enrollments/{eid}", headers=auth,
                               json={"status": "failed"})).status_code == 422


async def test_dismiss_handoff(client, portal, auth, other_auth, db):
    bill = await _confirmed_bill(db, await _user_id(auth))
    eid = (await client.post("/prize/enrollments/handoff", headers=auth,
                             json=handoff_body(str(bill.id)))).json()["enrollment_id"]

    assert (await client.patch(f"/prize/enrollments/{eid}", headers=other_auth,
                               json={"status": "dismissed"})).status_code == 404
    r = await client.patch(f"/prize/enrollments/{eid}", headers=auth, json={"status": "dismissed"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "dismissed"
    assert r.json()["prize_coupon_number"] is None

    # Dismissed: can't be re-closed, and the bill is free to hand off again as a new entry.
    assert (await client.patch(f"/prize/enrollments/{eid}", headers=auth,
                               json={"status": "submitted"})).status_code == 409
    r = await client.post("/prize/enrollments/handoff", headers=auth, json=handoff_body(str(bill.id)))
    assert r.status_code == 200 and r.json()["enrollment_id"] != eid


async def test_live_mode_direct_submit_refused(client, portal, auth, db, monkeypatch):
    monkeypatch.setattr(get_settings(), "prize_mode", "live")
    assert (await client.get("/prize/config")).json()["submission_mode"] == "handoff"
    bill = await _confirmed_bill(db, await _user_id(auth))
    r = await client.post("/prize/enrollments", headers=auth, json=body(str(bill.id)))
    assert r.status_code == 409 and "handoff" in r.json()["detail"]
    assert (await db.scalars(select(PrizeEnrollment))).first() is None


async def test_config_submission_mode_direct_in_test(client):
    assert (await client.get("/prize/config")).json()["submission_mode"] == "direct"



async def test_winners_proxy(client, portal, auth):
    r = await client.get("/prize/winners?limit=1&offset=0", headers=auth)
    assert r.status_code == 200
    out = r.json()
    assert out["total_draws"] == 2 and out["has_more"] is True
    assert out["draws"][0]["winners"][0]["prize_coupon_number"] == "000000000001"


async def test_my_wins_empty_without_enrollments(client, portal, auth):
    r = await client.get("/prize/my-wins", headers=auth)
    assert r.status_code == 200 and r.json() == []


async def test_my_wins_matches_submitted_coupon(client, portal, auth, db):
    bill = await _confirmed_bill(db, await _user_id(auth))
    sub = await client.post("/prize/enrollments", headers=auth, json=body(str(bill.id)))
    assert sub.status_code == 201, sub.text
    assert sub.json()["prize_coupon_number"] == "000000000001"
    r = await client.get("/prize/my-wins", headers=auth)
    assert r.status_code == 200, r.text
    wins = r.json()
    assert len(wins) == 1
    assert wins[0]["category_title_en"] == "Bumper Prize"
    assert wins[0]["bill_number"] == "BILL-9" and wins[0]["claim_open"] is True


async def test_winners_filters(client, portal, auth):
    def get(**q):
        return client.get("/prize/winners", headers=auth, params=q)

    out = (await get(category_id="category_daily")).json()
    assert out["total_draws"] == 1 and out["draws"][0]["category_title_en"] == "Daily Prize"
    assert out["categories"] and out["fiscal_years"]  # filter options ride along
    assert (await get(fiscal_year_code="2082-83")).json()["draws"] == []
    # from/to match draws whose whole eligibility window (09-17..10-01) lies inside the range
    assert (await get(**{"from": "2026-09-17", "to": "2026-10-01"})).json()["total_draws"] == 2
    assert (await get(**{"from": "2026-10-01", "to": "2026-10-09"})).json()["total_draws"] == 0


async def test_winners_rejects_bad_params(client, portal, auth):
    for q in ({"from": "nope"}, {"limit": 26}, {"category_id": "a b;c"}):
        r = await client.get("/prize/winners", headers=auth, params=q)
        assert r.status_code == 422, q


async def test_summary_counts_and_deadline(client, portal, auth, other_auth, db, monkeypatch):
    monkeypatch.setattr(prize_service, "nepal_today", lambda: dt.date(2026, 10, 11))
    monkeypatch.setattr(get_settings(), "prize_bumper_amount_npr", Decimal("10000000"))
    user = await _user_id(auth)
    sent = await _confirmed_bill(db, user, bill_number="SENT-1")
    await _confirmed_bill(db, user, bill_number="OPEN-1")
    await _confirmed_bill(db, user, bill_number="OLD-1", bill_date_ad=dt.date(2025, 1, 1))
    await _confirmed_bill(db, await _user_id(other_auth), bill_number="OTHER-1")
    assert (await client.post("/prize/enrollments", headers=auth,
                              json=body(str(sent.id)))).status_code == 201

    r = await client.get("/prize/summary", headers=auth)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["fiscal_year_code"] == "2083-84"
    assert out["entry_deadline"] == "2027-07-16" and out["days_left"] == 278
    assert out["coupons_this_year"] == 1
    assert out["coupons_all_time"] == 1
    assert out["eligible_this_year"] == 1  # OPEN-1; OLD-1 is from the closed fiscal year
    assert out["pending_handoffs"] == 0
    assert out["latest_bumper"]["draw_id"] == "draw_mock_bumper"
    assert out["latest_bumper"]["winner_count"] == 1
    assert out["latest_daily"]["draw_id"] == "draw_mock_daily"
    assert out["latest_daily"]["winner_count"] == 3
    assert Decimal(out["bumper_prize_amount_npr"]) == 10000000

    other = (await client.get("/prize/summary", headers=other_auth)).json()
    assert other["coupons_this_year"] == 0 and other["eligible_this_year"] == 1


async def test_summary_counts_pending_handoffs(client, portal, auth, db):
    bill = await _confirmed_bill(db, await _user_id(auth))
    assert (await client.post("/prize/enrollments/handoff", headers=auth,
                              json=handoff_body(str(bill.id)))).status_code == 200
    out = (await client.get("/prize/summary", headers=auth)).json()
    assert out["pending_handoffs"] == 1 and out["eligible_this_year"] == 0


async def test_summary_portal_down(client, auth, db):
    await _confirmed_bill(db, await _user_id(auth))

    def down(request):
        raise httpx.ConnectError("portal down", request=request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(down), base_url="http://down/api/v1")

    async def override():
        yield http

    from app.main import app as fastapi_app

    fastapi_app.dependency_overrides[get_prize_client] = override
    try:
        r = await client.get("/prize/summary", headers=auth)
    finally:
        fastapi_app.dependency_overrides.pop(get_prize_client, None)
        await http.aclose()
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["days_left"] is None and out["entry_deadline"] is None
    assert out["latest_bumper"] is None and out["latest_daily"] is None
    assert out["pending_handoffs"] == 0


async def test_summary_requires_auth(client, portal):
    assert (await client.get("/prize/summary")).status_code in (401, 403)
