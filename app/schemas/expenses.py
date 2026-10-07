import datetime as dt
import uuid
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.core import dates
from app.core.categories import Category
from app.core.payment_methods import PaymentMethod
from app.schemas.common import ORM, Amount, resolve_date


class ExpenseBill(BaseModel):
    """Tax-invoice details for a manual expense. The vendor name, bill date, total and
    payment method come from the expense itself (merchant, date, amount, payment_method)."""

    vendor_pan: str | None = Field(default=None, max_length=20)
    bill_number: str | None = Field(default=None, max_length=100)
    subtotal: Amount | None = None
    discount: Amount | None = None
    taxable_amount: Amount | None = None
    vat_amount: Amount | None = None

    def is_empty(self) -> bool:
        return all(v in (None, "") for v in self.model_dump().values())


class ExpenseIn(BaseModel):
    amount: Amount = Field(gt=0)
    category: Category
    merchant: str | None = Field(default=None, max_length=300)
    note: str | None = None
    payment_method: PaymentMethod | None = None
    expense_date: dt.date | None = None
    expense_date_bs: str | None = None
    bill: ExpenseBill | None = None

    @model_validator(mode="after")
    def _date(self) -> Self:
        resolve_date(self.expense_date, self.expense_date_bs)
        return self


class ExpenseUpdate(BaseModel):
    amount: Amount | None = Field(default=None, gt=0)
    category: Category | None = None
    merchant: str | None = Field(default=None, max_length=300)
    note: str | None = None
    payment_method: PaymentMethod | None = None
    expense_date: dt.date | None = None
    expense_date_bs: str | None = None
    # An empty object or null removes the expense's bill details.
    bill: ExpenseBill | None = None

    @model_validator(mode="after")
    def _date(self) -> Self:
        if self.expense_date is not None or self.expense_date_bs is not None:
            resolve_date(self.expense_date, self.expense_date_bs)
        return self


class ExpenseBillOut(ExpenseBill, ORM):
    vendor_name: str | None
    total_amount: Amount | None


class ExpenseOut(ORM):
    id: uuid.UUID
    amount: Amount
    category: str
    merchant: str | None
    note: str | None
    payment_method: str | None
    source: Literal["manual", "chat", "bill"]
    bill_id: uuid.UUID | None
    bill: ExpenseBillOut | None
    expense_date_ad: dt.date
    expense_date_bs: str
    created_at: dt.datetime

    @model_validator(mode="before")
    @classmethod
    def _bs(cls, obj):
        if hasattr(obj, "expense_date_ad"):
            bs = obj.expense_date_bs_raw
            if not bs:
                b = dates.ad_to_bs(obj.expense_date_ad)
                bs = dates.format_bs(b.year, b.month, b.day)
            return {**{c: getattr(obj, c) for c in cls.model_fields if c != "expense_date_bs"},
                    "expense_date_bs": bs}
        return obj
