import datetime as dt
import uuid

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import func, or_, select

from app.api.deps import CurrentUser, SessionDep, get_owned
from app.core.categories import Category
from app.core.payment_methods import PaymentMethod
from app.models import Expense
from app.schemas.expenses import ExpenseIn, ExpenseOut, ExpenseUpdate
from app.services import expenses

router = APIRouter(prefix="/expenses", tags=["expenses"])


@router.post("", response_model=ExpenseOut, status_code=status.HTTP_201_CREATED)
async def create_expense(body: ExpenseIn, user: CurrentUser, session: SessionDep):
    return await expenses.create_expense(session, user.id, body)


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
    return await expenses.update_expense(session, exp, body)


@router.delete("/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_expense(expense_id: uuid.UUID, user: CurrentUser, session: SessionDep):
    exp = await get_owned(session, Expense, expense_id, user)
    await expenses.delete_expense(session, exp)
