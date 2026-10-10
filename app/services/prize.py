"""Prize eligibility and the bumper-draw summary, shared by the REST routes and the chat agent."""

import datetime as dt
import time
import uuid
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.dates import draw_period
from app.models import Bill, PrizeEnrollment
from app.prize.client import PrizeError, fiscal_years, winners
from app.prize.validation import validate_fields
from app.schemas.prize import PrizeBumperDrawOut, PrizeSummaryOut

_NEPAL_TZ = ZoneInfo("Asia/Kathmandu")

_FISCAL_TTL_SECONDS = 3600.0
_fiscal_cache: dict | None = None
_fiscal_cached_at = 0.0

_WINNERS_TTL_SECONDS = 300.0
_WINNERS_CACHE_MAX = 200
_winners_cache: dict[tuple, tuple[float, dict]] = {}


def nepal_today() -> dt.date:
    """Today's date in Nepal, so day counts don't shift with the server's timezone."""
    return dt.datetime.now(_NEPAL_TZ).date()


async def cached_fiscal_years(http: httpx.AsyncClient) -> dict:
    global _fiscal_cache, _fiscal_cached_at
    now = time.monotonic()
    if _fiscal_cache is None or now - _fiscal_cached_at > _FISCAL_TTL_SECONDS:
        _fiscal_cache = await fiscal_years(http)
        _fiscal_cached_at = now
    return _fiscal_cache


async def cached_winners(http: httpx.AsyncClient, limit: int, offset: int, **filters) -> dict:
    key = (limit, offset, tuple(sorted(filters.items())))
    hit = _winners_cache.get(key)
    if hit and time.monotonic() - hit[0] <= _WINNERS_TTL_SECONDS:
        return hit[1]
    page = await winners(http, limit, offset, **filters)
    if len(_winners_cache) >= _WINNERS_CACHE_MAX:
        _winners_cache.clear()
    _winners_cache[key] = (time.monotonic(), page)
    return page


def period_open(bill_date: dt.date, today: dt.date | None = None) -> bool:
    """Whether a bill can still be entered: IRD only takes it until the end of its own draw
    period, it can't roll into a later draw."""
    return draw_period(bill_date).end >= (today or nepal_today())


def reset_portal_caches() -> None:
    global _fiscal_cache, _fiscal_cached_at
    _fiscal_cache, _fiscal_cached_at = None, 0.0
    _winners_cache.clear()


async def eligible_bills(session: AsyncSession, user_id: uuid.UUID) -> list[Bill]:
    """Confirmed bills that pass the portal's bill checks, are in a draw period still open
    for entry and aren't already enrolled, newest first."""
    today = nepal_today()
    taken = {
        (r.seller_pan_no, r.bill_number, r.bill_date)
        for r in await session.scalars(select(PrizeEnrollment).where(
            PrizeEnrollment.user_id == user_id,
            PrizeEnrollment.status.in_(("submitted", "handed_off")),
        ))
    }
    bills = await session.scalars(select(Bill).where(
        Bill.user_id == user_id, Bill.status == "confirmed",
        Bill.vendor_pan.is_not(None), Bill.bill_number.is_not(None),
        Bill.bill_date_ad.is_not(None), Bill.total_amount.is_not(None),
        or_(Bill.payment_method.is_(None), Bill.payment_method == "cash"),
    ).order_by(Bill.bill_date_ad.desc()))
    return [
        b for b in bills
        if (b.vendor_pan, b.bill_number, b.bill_date_ad) not in taken
        and period_open(b.bill_date_ad, today)
        and not validate_fields(
            bill_number=b.bill_number, seller_pan_no=b.vendor_pan, bill_date=b.bill_date_ad,
            billed_total_amount=float(b.total_amount), payment_method="Cash",
            name="Placeholder", address="Placeholder", mobile_number="9800000000")
    ]


async def pending_handoffs(
    session: AsyncSession, user_id: uuid.UUID
) -> list[tuple[PrizeEnrollment, str | None]]:
    """Handed-off enrollments the user hasn't marked submitted yet, with the bill's vendor
    name (None if the bill was deleted), newest first."""
    rows = await session.execute(
        select(PrizeEnrollment, Bill.vendor_name)
        .outerjoin(Bill, Bill.id == PrizeEnrollment.bill_id)
        .where(PrizeEnrollment.user_id == user_id, PrizeEnrollment.status == "handed_off")
        .order_by(PrizeEnrollment.created_at.desc())
    )
    return [(e, vendor) for e, vendor in rows]


def active_fiscal_year(years: list[dict]) -> dict | None:
    return next((y for y in years if y.get("status") == "ACTIVE"), None)


async def _latest_category_draw(
    http: httpx.AsyncClient, categories: list[dict], keyword: str
) -> PrizeBumperDrawOut | None:
    """Latest draw of the category whose title contains `keyword` ("bumper" or "daily")."""
    cat = next((c for c in categories if keyword in (c.get("title_en") or "").lower()), None)
    if cat is None:
        return None
    draws = (await cached_winners(http, 1, 0, category_id=cat["category_id"])).get("draws", [])
    if not draws:
        return None
    d = draws[0]
    return PrizeBumperDrawOut(
        draw_id=d["draw_id"], title_en=d.get("title_en", ""), title_ne=d.get("title_ne"),
        eligible_from=d.get("eligible_from"), eligible_to=d.get("eligible_to"),
        published_at=d.get("published_at"), claim_deadline=d.get("claim_deadline"),
        claim_open=bool(d.get("claim_open")), winner_count=len(d.get("winners", [])),
    )


async def prize_summary(
    session: AsyncSession, user_id: uuid.UUID, http: httpx.AsyncClient
) -> PrizeSummaryOut:
    """The current draw period's entry deadline and draw date plus the user's standing. The
    fiscal year and the latest draw (bumper and daily prizes) come from the portal and are
    left empty if it's unreachable."""
    today = nepal_today()
    period = draw_period(today)
    out = PrizeSummaryOut(
        bumper_prize_amount_npr=get_settings().prize_bumper_amount_npr,
        period_start=period.start, entry_deadline=period.end, draw_date=period.draw_date,
        days_left=(period.end - today).days,
        eligible_this_year=len(await eligible_bills(session, user_id)),
    )
    try:
        years = (await cached_fiscal_years(http)).get("fiscal_years") or []
    except PrizeError:
        years = []
    fy = active_fiscal_year(years)
    if fy:
        start, end = dt.date.fromisoformat(fy["start_date"]), dt.date.fromisoformat(fy["end_date"])
        out.fiscal_year_code = fy["fiscal_year_code"]
        out.fiscal_year_name = fy.get("display_name") or fy["fiscal_year_code"]
        out.coupons_this_year = len(list(await session.scalars(select(PrizeEnrollment.id).where(
            PrizeEnrollment.user_id == user_id, PrizeEnrollment.status == "submitted",
            PrizeEnrollment.bill_date.between(start, end)))))
    out.coupons_all_time = len(list(await session.scalars(select(PrizeEnrollment.id).where(
        PrizeEnrollment.user_id == user_id, PrizeEnrollment.status == "submitted"))))
    out.pending_handoffs = len(await pending_handoffs(session, user_id))
    try:
        categories = (await cached_winners(http, 1, 0)).get("categories", [])
        out.latest_bumper = await _latest_category_draw(http, categories, "bumper")
        out.latest_daily = await _latest_category_draw(http, categories, "daily")
    except PrizeError:
        pass
    return out
