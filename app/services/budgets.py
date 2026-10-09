"""Category budgets, shared by the REST routes and the chat agent's tools.

A budget is a monthly limit per (user, category), checked against the current BS month.
"""

import uuid
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import dates
from app.core.categories import Category
from app.models import Budget, Expense

ZERO = Decimal("0.00")
WARN_AT = 80.0  # percent of the budget at which the status turns "warning"


def _status(percent: float) -> str:
    return "over" if percent > 100 else "warning" if percent >= WARN_AT else "ok"


async def upsert_budget(session: AsyncSession, user_id: uuid.UUID, category: Category,
                        amount: Decimal) -> Budget:
    budget = await session.scalar(
        select(Budget).where(Budget.user_id == user_id, Budget.category == category.value))
    if budget is None:
        budget = Budget(user_id=user_id, category=category.value, amount=amount)
        session.add(budget)
    else:
        budget.amount = amount
    await session.commit()
    await session.refresh(budget)
    return budget


async def delete_budget(session: AsyncSession, user_id: uuid.UUID, category: Category) -> bool:
    """Remove a budget; False when the user had none for that category."""
    result = await session.execute(
        delete(Budget).where(Budget.user_id == user_id, Budget.category == category.value))
    await session.commit()
    return result.rowcount > 0


async def budget_status(session: AsyncSession, user_id: uuid.UUID) -> list[dict]:
    """Every budget with this BS month's spend, biggest share of its limit first."""
    budgets = list(await session.scalars(select(Budget).where(Budget.user_id == user_id)))
    if not budgets:
        return []
    start, end = dates.month_bounds(*dates.today_bs())
    spent = dict((await session.execute(
        select(Expense.category, func.sum(Expense.amount))
        .where(Expense.user_id == user_id, Expense.expense_date_ad.between(start, end))
        .group_by(Expense.category)
    )).all())
    rows = []
    for b in budgets:
        used = spent.get(b.category) or ZERO
        percent = round(float(used / b.amount * 100), 1)
        rows.append({"category": b.category, "amount": b.amount, "spent": used,
                     "remaining": b.amount - used, "percent": percent,
                     "status": _status(percent)})
    return sorted(rows, key=lambda r: r["percent"], reverse=True)
