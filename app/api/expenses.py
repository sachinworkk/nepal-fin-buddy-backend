import datetime as dt
import uuid

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import func, or_, select

from app.api.deps import CurrentUser, SessionDep, get_owned
from app.core.categories import Category
from app.core.payment_methods import PaymentMethod
from app.models import Bill, Expense
from app.schemas.common import resolve_date
from app.schemas.expenses import ExpenseBill, ExpenseIn, ExpenseOut, ExpenseUpdate

router = APIRouter(prefix="/expenses", tags=["expenses"])


async def _set_bill_details(session, exp: Expense, details: ExpenseBill | None) -> None:
    """Create, update or (when empty) remove the image-less bill behind a manual expense."""
    if details is None or details.is_empty():
        if exp.bill is not None:
            await session.delete(exp.bill)  # prize enrollments keep their own copy
            exp.bill = None
        return
    if exp.bill is None:
        exp.bill = Bill(user_id=exp.user_id, status="confirmed")
    for k, v in details.model_dump().items():
        setattr(exp.bill, k, None if v == "" else v)


def _set_scanned_bill_details(exp: Expense, details: ExpenseBill | None) -> None:
    """Fill in or correct a scanned bill's tax-invoice details. The bill itself is never
    removed here, and its vendor, total and date stay as scanned."""
    for k, v in (details or ExpenseBill()).model_dump().items():
        setattr(exp.bill, k, None if v == "" else v)


def _sync_bill(exp: Expense) -> None:
    """Mirror the expense's vendor, total, date and payment method onto its manual bill."""
    if exp.bill is None or exp.source == "bill":
        return
    b = exp.bill
    b.vendor_name, b.total_amount, b.payment_method = exp.merchant, exp.amount, exp.payment_method
    b.bill_date_ad, b.bill_date_bs_raw = exp.expense_date_ad, exp.expense_date_bs_raw


@router.post("", response_model=ExpenseOut, status_code=status.HTTP_201_CREATED)
async def create_expense(body: ExpenseIn, user: CurrentUser, session: SessionDep):
    ad, bs = resolve_date(body.expense_date, body.expense_date_bs)
    exp = Expense(
        user_id=user.id, amount=body.amount, category=body.category.value,
        merchant=body.merchant, note=body.note, payment_method=body.payment_method,
        expense_date_ad=ad, expense_date_bs_raw=bs if body.expense_date_bs else None,
        source="manual", bill=None,
    )
    await _set_bill_details(session, exp, body.bill)
    _sync_bill(exp)
    session.add(exp)
    await session.commit()
    await session.refresh(exp)
    return exp


@router.get("", response_model=list[ExpenseOut])
async def list_expenses(
    user: CurrentUser, session: SessionDep, response: Response,
    date_from: dt.date | None = None, date_to: dt.date | None = None,
    category: Category | None = None, payment_method: PaymentMethod | None = None,
    source: str | None = None, q: str | None = None,
    limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
):
    cond = [Expense.user_id == user.id]
    if date_from:
        cond.append(Expense.expense_date_ad >= date_from)
    if date_to:
        cond.append(Expense.expense_date_ad <= date_to)
    if category:
        cond.append(Expense.category == category.value)
    if payment_method:
        cond.append(Expense.payment_method == payment_method.value)
    if source:
        cond.append(Expense.source == source)
    if q:
        like = f"%{q}%"
        cond.append(or_(Expense.merchant.ilike(like), Expense.note.ilike(like)))
    total = await session.scalar(select(func.count()).select_from(Expense).where(*cond))
    response.headers["X-Total-Count"] = str(total)
    rows = await session.scalars(
        select(Expense).where(*cond)
        .order_by(Expense.expense_date_ad.desc(), Expense.created_at.desc())
        .limit(limit).offset(offset)
    )
    return list(rows)


@router.get("/{expense_id}", response_model=ExpenseOut)
async def get_expense(expense_id: uuid.UUID, user: CurrentUser, session: SessionDep):
    return await get_owned(session, Expense, expense_id, user)


@router.patch("/{expense_id}", response_model=ExpenseOut)
async def update_expense(
    expense_id: uuid.UUID, body: ExpenseUpdate, user: CurrentUser, session: SessionDep
):
    exp = await get_owned(session, Expense, expense_id, user)
    data = body.model_dump(exclude_unset=True, exclude={"bill"})
    if "bill" in body.model_fields_set:
        if exp.source == "bill" and exp.bill is not None:
            _set_scanned_bill_details(exp, body.bill)
        else:
            if exp.source == "bill" and body.bill is not None and not body.bill.is_empty():
                exp.source = "manual"  # its scanned bill was deleted; the new bill is manual
            await _set_bill_details(session, exp, body.bill)
    date_ad, date_bs = data.pop("expense_date", None), data.pop("expense_date_bs", None)
    if date_ad is not None or date_bs is not None:
        exp.expense_date_ad, bs = resolve_date(date_ad, date_bs)
        exp.expense_date_bs_raw = bs if date_bs else None
    if "category" in data and data["category"] is not None:
        data["category"] = data["category"].value
    for k, v in data.items():
        setattr(exp, k, v)
    _sync_bill(exp)
    await session.commit()
    await session.refresh(exp)
    return exp


@router.delete("/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_expense(expense_id: uuid.UUID, user: CurrentUser, session: SessionDep):
    exp = await get_owned(session, Expense, expense_id, user)
    if exp.bill is not None and exp.source != "bill":
        await session.delete(exp.bill)  # a manual expense's bill has no life of its own
    await session.delete(exp)
    await session.commit()
