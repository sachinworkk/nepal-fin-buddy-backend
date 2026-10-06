import datetime as dt
import uuid
from decimal import Decimal
from typing import Self

from pydantic import BaseModel, Field, computed_field, model_validator

from app.core.categories import Category
from app.extraction import bill_checks
from app.schemas.common import ORM, Amount, resolve_date


class BillItemIn(BaseModel):
    description: str = Field(max_length=500)
    quantity: Decimal | None = None
    unit_price: Amount | None = None
    amount: Amount | None = None


class BillItemOut(BillItemIn, ORM):
    id: uuid.UUID


class BillFields(BaseModel):
    vendor_name: str | None = Field(default=None, max_length=300)
    vendor_pan: str | None = Field(default=None, max_length=20)
    bill_number: str | None = Field(default=None, max_length=100)
    bill_date_ad: dt.date | None = None
    bill_date_bs_raw: str | None = Field(default=None, max_length=20)
    subtotal: Amount | None = None
    discount: Amount | None = None
    taxable_amount: Amount | None = None
    vat_amount: Amount | None = None
    total_amount: Amount | None = None


class BillUpdate(BillFields):
    items: list[BillItemIn] | None = None


class BillConfirm(BillUpdate):
    category: Category
    total_amount: Amount = Field(gt=0)
    bill_date_bs: str | None = None

    @model_validator(mode="after")
    def _date(self) -> Self:
        resolve_date(self.bill_date_ad, self.bill_date_bs)  # exactly one, valid
        return self


class BillOut(BillFields, ORM):
    id: uuid.UUID
    status: str
    extraction_confidence: float | None
    created_at: dt.datetime
    items: list[BillItemOut]
    image_path: str | None = Field(default=None, exclude=True)
    # Set by the bills API: True when the bill has any prize-lottery enrollment row.
    prize_enrolled: bool = False

    @computed_field
    @property
    def has_image(self) -> bool:
        return bool(self.image_path)

    @computed_field
    @property
    def flags(self) -> list[str]:
        return bill_checks(self.model_dump(exclude={"flags", "has_image"}))[1]
