import datetime as dt
import uuid

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import func, or_, select

from app.api.deps import CurrentUser, SessionDep, get_owned
from app.core.categories import Category
from app.models import Expense
from app.schemas.common import resolve_date
from app.schemas.expenses import ExpenseIn, ExpenseOut, ExpenseUpdate

router = APIRouter(prefix="/expenses", tags=["expenses"])


@router.post("", response_model=ExpenseOut, status_code=status.HTTP_201_CREATED)
async def create_expense(body: ExpenseIn, user: CurrentUser, session: SessionDep):
    ad, bs = resolve_date(body.expense_date, body.expense_date_bs)
    exp = Expense(
        user_id=user.id, amount=body.amount, category=body.category.value,
        merchant=body.merchant, note=body.note, payment_method=body.payment_method,
        expense_date_ad=ad, expense_date_bs_raw=bs if body.expense_date_bs else None,
        source="manual",
    )
    session.add(exp)
    await session.commit()
    await session.refresh(exp)
    return exp


@router.get("", response_model=list[ExpenseOut])
async def list_expenses(
    user: CurrentUser, session: SessionDep, response: Response,
    date_from: dt.date | None = None, date_to: dt.date | None = None,
    category: Category | None = None, source: str | None = None, q: str | None = None,
    limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
):
    cond = [Expense.user_id == user.id]
    if date_from:
        cond.append(Expense.expense_date_ad >= date_from)
    if date_to:
        cond.append(Expense.expense_date_ad <= date_to)
    if category:
        cond.append(Expense.category == category.value)
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
    data = body.model_dump(exclude_unset=True)
    date_ad, date_bs = data.pop("expense_date", None), data.pop("expense_date_bs", None)
    if date_ad is not None or date_bs is not None:
        exp.expense_date_ad, bs = resolve_date(date_ad, date_bs)
        exp.expense_date_bs_raw = bs if date_bs else None
    if "category" in data and data["category"] is not None:
        data["category"] = data["category"].value
    for k, v in data.items():
        setattr(exp, k, v)
    await session.commit()
    await session.refresh(exp)
    return exp


@router.delete("/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_expense(expense_id: uuid.UUID, user: CurrentUser, session: SessionDep):
    await session.delete(await get_owned(session, Expense, expense_id, user))
    await session.commit()
