from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import Amount


class BudgetIn(BaseModel):
    amount: Amount = Field(gt=0)


class BudgetOut(BaseModel):
    """A category's monthly limit with this BS month's spend against it."""

    category: str
    amount: Decimal
    spent: Decimal
    remaining: Decimal  # negative once over budget
    percent: float
    status: Literal["ok", "warning", "over"]
