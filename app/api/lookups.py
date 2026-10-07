from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.models import Category, PaymentMethod
from app.schemas.lookups import CategoryOut, PaymentMethodOut

router = APIRouter(tags=["lookups"])


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(user: CurrentUser, session: SessionDep):
    return list(await session.scalars(select(Category).order_by(Category.sort_order)))


@router.get("/payment-methods", response_model=list[PaymentMethodOut])
async def list_payment_methods(user: CurrentUser, session: SessionDep):
    return list(await session.scalars(select(PaymentMethod).order_by(PaymentMethod.sort_order)))
