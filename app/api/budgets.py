from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import CurrentUser, SessionDep
from app.core.categories import Category
from app.schemas.budgets import BudgetIn, BudgetOut
from app.services import budgets

router = APIRouter(prefix="/budgets", tags=["budgets"])


@router.get("", response_model=list[BudgetOut])
async def list_budgets(user: CurrentUser, session: SessionDep):
    """The user's monthly budgets with this BS month's spend against each."""
    return await budgets.budget_status(session, user.id)


@router.put("/{category}", response_model=BudgetOut)
async def set_budget(category: Category, body: BudgetIn, user: CurrentUser, session: SessionDep):
    """Create or replace the monthly budget for a category."""
    await budgets.upsert_budget(session, user.id, category, body.amount)
    return next(r for r in await budgets.budget_status(session, user.id)
                if r["category"] == category.value)


@router.delete("/{category}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_budget(category: Category, user: CurrentUser, session: SessionDep):
    if not await budgets.delete_budget(session, user.id, category):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No budget for that category")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
