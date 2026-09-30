import datetime as dt
from collections import defaultdict
from decimal import Decimal

import nepali_datetime
from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, SessionDep
from app.core import dates
from app.models import Expense
from app.schemas.stats import CategoryTotal, MerchantTotal, MonthTotal, Summary

router = APIRouter(prefix="/stats", tags=["stats"])
ZERO = Decimal("0.00")


def _month_bounds(y: int, m: int) -> tuple[dt.date, dt.date]:
    start = dates.bs_to_ad(y, m, 1)
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    return start, dates.bs_to_ad(ny, nm, 1) - dt.timedelta(days=1)


def _label(y: int, m: int) -> str:
    return f"{y} {nepali_datetime.date(y, m, 1).strftime('%B')}"


def _shift(y: int, m: int, delta: int) -> tuple[int, int]:
    idx = y * 12 + (m - 1) + delta
    return idx // 12, idx % 12 + 1


async def _month_total(session, user, y: int, m: int) -> Decimal:
    start, end = _month_bounds(y, m)
    return await session.scalar(
        select(func.coalesce(func.sum(Expense.amount), 0)).where(
            Expense.user_id == user.id, Expense.expense_date_ad.between(start, end))
    ) or ZERO


def _today_bs() -> tuple[int, int]:
    b = dates.ad_to_bs(dt.date.today())
    return b.year, b.month


@router.get("/monthly", response_model=list[MonthTotal])
async def monthly(user: CurrentUser, session: SessionDep, months: int = Query(6, ge=1, le=24)):
    """Totals per BS month, oldest first. Days are folded into BS months in Python."""
    cy, cm = _today_bs()
    fy, fm = _shift(cy, cm, -(months - 1))
    window_start = _month_bounds(fy, fm)[0]
    rows = await session.execute(
        select(Expense.expense_date_ad, func.sum(Expense.amount))
        .where(Expense.user_id == user.id, Expense.expense_date_ad >= window_start)
        .group_by(Expense.expense_date_ad)
    )
    totals: dict[tuple[int, int], Decimal] = defaultdict(lambda: ZERO)
    for day, amount in rows:
        b = dates.ad_to_bs(day)
        totals[(b.year, b.month)] += amount
    out = []
    for i in range(months):
        y, m = _shift(fy, fm, i)
        start, end = _month_bounds(y, m)
        out.append(MonthTotal(label=_label(y, m), bs_year=y, bs_month=m,
                              start=start, end=end, total=totals[(y, m)]))
    return out


@router.get("/by-category", response_model=list[CategoryTotal])
async def by_category(
    user: CurrentUser, session: SessionDep,
    date_from: dt.date | None = None, date_to: dt.date | None = None,
):
    cond = [Expense.user_id == user.id]
    if date_from:
        cond.append(Expense.expense_date_ad >= date_from)
    if date_to:
        cond.append(Expense.expense_date_ad <= date_to)
    rows = (await session.execute(
        select(Expense.category, func.sum(Expense.amount), func.count())
        .where(*cond).group_by(Expense.category)
        .order_by(func.sum(Expense.amount).desc())
    )).all()
    grand = sum((r[1] for r in rows), ZERO)
    return [CategoryTotal(category=c, total=t, count=n,
                          share=float(t / grand) if grand else 0.0) for c, t, n in rows]


@router.get("/summary", response_model=Summary)
async def summary(user: CurrentUser, session: SessionDep):
    y, m = _today_bs()
    py, pm = _shift(y, m, -1)
    start, end = _month_bounds(y, m)
    merchants = (await session.execute(
        select(Expense.merchant, func.sum(Expense.amount).label("t"))
        .where(Expense.user_id == user.id, Expense.merchant.is_not(None),
               Expense.expense_date_ad.between(start, end))
        .group_by(Expense.merchant).order_by(func.sum(Expense.amount).desc()).limit(5)
    )).all()
    return Summary(
        month_label=_label(y, m),
        this_month=await _month_total(session, user, y, m),
        last_month=await _month_total(session, user, py, pm),
        top_merchants=[MerchantTotal(merchant=n, total=t) for n, t in merchants],
    )
