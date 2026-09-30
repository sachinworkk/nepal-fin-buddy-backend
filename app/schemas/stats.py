import datetime as dt
from decimal import Decimal

from pydantic import BaseModel


class MonthTotal(BaseModel):
    label: str
    bs_year: int
    bs_month: int
    start: dt.date
    end: dt.date
    total: Decimal


class CategoryTotal(BaseModel):
    category: str
    total: Decimal
    count: int
    share: float


class MerchantTotal(BaseModel):
    merchant: str
    total: Decimal


class Summary(BaseModel):
    month_label: str
    this_month: Decimal
    last_month: Decimal
    top_merchants: list[MerchantTotal]
