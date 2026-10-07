import datetime as dt
import time
import uuid
from decimal import Decimal
from typing import Annotated

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import or_, select

from app.api.deps import CurrentUser, SessionDep, get_owned
from app.config import get_settings
from app.models import Bill, PrizeEnrollment
from app.prize.client import PrizeError, fiscal_years, get_prize_client, submit_enrollment
from app.prize.turnstile import TurnstileError, verify_turnstile
from app.prize.validation import validate_fields
from app.schemas.prize import (
    PrizeConfigOut,
    PrizeEligibleOut,
    PrizeEnrollmentIn,
    PrizeEnrollmentOut,
    PrizeHandoffIn,
    PrizeHandoffOut,
    PrizeMarkSubmittedIn,
)

log = structlog.get_logger()
router = APIRouter(prefix="/prize", tags=["prize"])

PrizeClient = Annotated[httpx.AsyncClient, Depends(get_prize_client)]

_FISCAL_TTL_SECONDS = 3600.0
_fiscal_cache: dict | None = None
_fiscal_cached_at = 0.0

# user_id -> last monotonic time an IRD submission was attempted. IRD throttles
# bursts with a misleading "form expired" error; we space submissions out instead.
_last_submit_at: dict[uuid.UUID, float] = {}


@router.get("/config", response_model=PrizeConfigOut)
async def prize_config() -> PrizeConfigOut:
    """Public bits the frontend needs to render the Turnstile widget."""
    s = get_settings()
    return PrizeConfigOut(
        turnstile_sitekey=s.prize_turnstile_sitekey, payment_methods=["Cash"],
        submission_mode="handoff" if s.prize_mode == "live" else "direct",
    )


async def _cached_fiscal_years(http: httpx.AsyncClient) -> dict:
    global _fiscal_cache, _fiscal_cached_at
    now = time.monotonic()
    if _fiscal_cache is None or now - _fiscal_cached_at > _FISCAL_TTL_SECONDS:
        _fiscal_cache = await fiscal_years(http)
        _fiscal_cached_at = now
    return _fiscal_cache


@router.get("/fiscal-years")
async def prize_fiscal_years(user: CurrentUser, http: PrizeClient) -> dict:
    """Proxy the portal's fiscal-year list (one hour in-memory cache)."""
    return await _cached_fiscal_years(http)


def _check_form_started_at(form_started_at: int) -> None:
    s = get_settings()
    age = time.time() - form_started_at / 1000
    if age < s.prize_min_form_age_seconds or age > s.prize_max_form_age_seconds:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"form_started_at must be between {s.prize_min_form_age_seconds:g}s and "
            f"{s.prize_max_form_age_seconds / 3600:g}h before now",
        )


async def _resolve_fields(
    session, body: PrizeEnrollmentIn, user
) -> tuple[dict[str, str], uuid.UUID, dt.date, Decimal]:
    """Prefill bill fields from the confirmed bill; 422 on anything missing/invalid."""
    bill = await get_owned(session, Bill, body.bill_id, user)
    if bill.status != "confirmed":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Bill must be confirmed before submitting"
        )
    vals: dict[str, str | dt.date | Decimal | None] = {
        "bill_number": bill.bill_number,
        "seller_pan_no": bill.vendor_pan,
        "bill_date": bill.bill_date_ad,
        "billed_total_amount": bill.total_amount,
        "payment_method": body.payment_method,
    }

    missing = [k for k, v in vals.items() if v is None]
    if missing:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {k: "is required" for k in missing},
        )

    errors = validate_fields(
        bill_number=vals["bill_number"], seller_pan_no=vals["seller_pan_no"],
        bill_date=vals["bill_date"], billed_total_amount=float(vals["billed_total_amount"]),
        payment_method=vals["payment_method"], name=body.name, address=body.address,
        mobile_number=body.mobile_number,
    )
    if errors:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, errors)

    fields = {
        "bill_number": str(vals["bill_number"]),
        "seller_pan_no": str(vals["seller_pan_no"]),
        "bill_date": vals["bill_date"].isoformat(),
        "billed_total_amount": f"{vals['billed_total_amount']:f}",
        "payment_method": "Cash",
        "name": body.name.strip(),
        "address": body.address.strip(),
        "mobile_number": body.mobile_number,
        "company_website": (body.company_website or "").strip(),
    }
    return fields, bill.id, vals["bill_date"], vals["billed_total_amount"]


@router.post("/enrollments", response_model=PrizeEnrollmentOut,
             status_code=status.HTTP_201_CREATED)
async def create_enrollment(
    body: PrizeEnrollmentIn, user: CurrentUser, session: SessionDep, http: PrizeClient,
    request: Request,
) -> PrizeEnrollmentOut:
    if get_settings().prize_mode == "live":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Live submission isn't supported - use /prize/enrollments/handoff",
        )
    _check_form_started_at(body.form_started_at)
    remoteip = request.client.host if request.client else None
    try:
        await verify_turnstile(body.turnstile_token, remoteip)
    except TurnstileError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e)) from None
    fields, bill_id, bill_date, amount = await _resolve_fields(session, body, user)

    existing = await session.scalar(select(PrizeEnrollment).where(
        PrizeEnrollment.user_id == user.id,
        PrizeEnrollment.seller_pan_no == fields["seller_pan_no"],
        PrizeEnrollment.bill_number == fields["bill_number"],
        PrizeEnrollment.bill_date == bill_date,
        PrizeEnrollment.status == "submitted",
    ))
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "This bill has already been submitted")

    s = get_settings()
    now = time.monotonic()
    last = _last_submit_at.get(user.id)
    if last is not None and now - last < s.prize_user_submit_interval_seconds:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "Please wait a moment before submitting again")
    _last_submit_at[user.id] = now

    row = PrizeEnrollment(
        user_id=user.id, bill_id=bill_id, seller_pan_no=fields["seller_pan_no"],
        bill_number=fields["bill_number"], bill_date=bill_date,
        billed_total_amount=amount, payment_method=fields["payment_method"],
        name=fields["name"], address=fields["address"],
        mobile_number=fields["mobile_number"], company_website=fields["company_website"]
        or None, form_started_at=body.form_started_at,
    )
    try:
        resp = await submit_enrollment(http, fields, body.turnstile_token, body.form_started_at)
    except PrizeError as e:
        row.status, row.last_error = "failed", e.message
        session.add(row)
        await session.commit()
        log.warning("prize.enrollment_failed", user_id=str(user.id),
                    portal_status=e.status_code, error=e.message)
        if e.status_code is not None and e.status_code < 500:
            # Portal-side validation/security rejection (400). Other client errors
            # (e.g. throttling) surface as-is so the frontend can prompt a retry.
            raise HTTPException(e.status_code, e.message) from None
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, e.message) from None

    row.status = "submitted"
    row.prize_coupon_number = resp.get("prize_coupon_number") or resp.get("couponNumber")
    row.verification_id = resp.get("verification_id") or resp.get("verificationId")
    row.duplicate = bool(resp.get("duplicate"))
    row.ird_response = resp
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def _find_active(
    session, user, key: tuple[str, str, dt.date], *statuses: str
) -> PrizeEnrollment | None:
    """The user's enrollment for the same PAN + bill number + date in one of `statuses`."""
    pan, number, date = key
    return await session.scalar(select(PrizeEnrollment).where(
        PrizeEnrollment.user_id == user.id,
        PrizeEnrollment.seller_pan_no == pan,
        PrizeEnrollment.bill_number == number,
        PrizeEnrollment.bill_date == date,
        PrizeEnrollment.status.in_(statuses),
    ))


@router.get("/eligible", response_model=list[PrizeEligibleOut])
async def list_eligible(user: CurrentUser, session: SessionDep, http: PrizeClient):
    """Confirmed bills that pass the portal's bill checks and aren't already enrolled."""
    taken = {
        (r.seller_pan_no, r.bill_number, r.bill_date)
        for r in await session.scalars(select(PrizeEnrollment).where(
            PrizeEnrollment.user_id == user.id,
            PrizeEnrollment.status.in_(("submitted", "handed_off")),
        ))
    }
    bills = await session.scalars(select(Bill).where(
        Bill.user_id == user.id, Bill.status == "confirmed",
        Bill.vendor_pan.is_not(None), Bill.bill_number.is_not(None),
        Bill.bill_date_ad.is_not(None), Bill.total_amount.is_not(None),
        or_(Bill.payment_method.is_(None), Bill.payment_method == "cash"),
    ).order_by(Bill.bill_date_ad.desc()))

    try:
        years = (await _cached_fiscal_years(http)).get("fiscal_years") or []
    except PrizeError:
        years = []  # the list is still useful without deadlines

    today = dt.date.today()
    out = []
    for b in bills:
        if (b.vendor_pan, b.bill_number, b.bill_date_ad) in taken:
            continue
        if validate_fields(
            bill_number=b.bill_number, seller_pan_no=b.vendor_pan, bill_date=b.bill_date_ad,
            billed_total_amount=float(b.total_amount), payment_method="Cash",
            name="Placeholder", address="Placeholder", mobile_number="9800000000",
        ):
            continue
        fy = next((y for y in years
                   if y["start_date"] <= b.bill_date_ad.isoformat() <= y["end_date"]), None)
        active = fy is not None and fy.get("status") == "ACTIVE"
        out.append(PrizeEligibleOut(
            bill_id=b.id, vendor_name=b.vendor_name, seller_pan_no=b.vendor_pan,
            bill_number=b.bill_number, bill_date=b.bill_date_ad,
            billed_total_amount=b.total_amount,
            fiscal_year_code=fy["fiscal_year_code"] if fy else None,
            fiscal_year_status=fy.get("status") if fy else None,
            days_left=max(0, (dt.date.fromisoformat(fy["end_date"]) - today).days)
            if active else None,
        ))
    return out


@router.post("/enrollments/handoff", response_model=PrizeHandoffOut)
async def create_handoff(
    body: PrizeHandoffIn, user: CurrentUser, session: SessionDep,
) -> PrizeHandoffOut:
    """Prefill a bill's portal fields for the user to submit on the IRD portal themselves."""
    fields, bill_id, bill_date, amount = await _resolve_fields(session, body, user)
    key = (fields["seller_pan_no"], fields["bill_number"], bill_date)
    if await _find_active(session, user, key, "submitted"):
        raise HTTPException(status.HTTP_409_CONFLICT, "This bill has already been submitted")

    row = await _find_active(session, user, key, "handed_off")
    if row is None:
        row = PrizeEnrollment(
            user_id=user.id, bill_id=bill_id, seller_pan_no=key[0], bill_number=key[1],
            bill_date=bill_date, billed_total_amount=amount,
            payment_method=fields["payment_method"], status="handed_off",
            form_started_at=int(time.time() * 1000),
        )
    row.name, row.address = fields["name"], fields["address"]
    row.mobile_number = fields["mobile_number"]
    row.company_website = fields["company_website"] or None
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return PrizeHandoffOut(
        enrollment_id=row.id, fields=fields, portal_url=get_settings().prize_portal_url,
    )


@router.patch("/enrollments/{enrollment_id}", response_model=PrizeEnrollmentOut)
async def mark_submitted(
    enrollment_id: uuid.UUID, body: PrizeMarkSubmittedIn, user: CurrentUser, session: SessionDep,
):
    """The user finished the portal form; optionally record the coupon number they got."""
    row = await get_owned(session, PrizeEnrollment, enrollment_id, user)
    if row.status != "handed_off":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Only a handed-off bill can be marked submitted"
        )
    row.status = "submitted"
    row.prize_coupon_number = body.prize_coupon_number
    await session.commit()
    await session.refresh(row)
    return row


@router.get("/enrollments", response_model=list[PrizeEnrollmentOut])
async def list_enrollments(
    user: CurrentUser, session: SessionDep, bill_id: uuid.UUID | None = None
):
    q = select(PrizeEnrollment).where(PrizeEnrollment.user_id == user.id)
    if bill_id is not None:
        q = q.where(PrizeEnrollment.bill_id == bill_id)
    return list(await session.scalars(q.order_by(PrizeEnrollment.created_at.desc())))


@router.get("/enrollments/{enrollment_id}", response_model=PrizeEnrollmentOut)
async def get_enrollment(enrollment_id: uuid.UUID, user: CurrentUser, session: SessionDep):
    return await get_owned(session, PrizeEnrollment, enrollment_id, user)