import datetime as dt
import uuid
from decimal import Decimal
from typing import Self

from pydantic import BaseModel, Field, computed_field, model_validator

from app.cbms import messages
from app.schemas.common import ORM, Amount


class InvoiceIn(BaseModel):
    is_return: bool = False
    invoice_number: str = Field(min_length=1, max_length=50)
    invoice_date_bs: str
    buyer_name: str = Field(min_length=1, max_length=300)
    buyer_pan: str | None = Field(default=None, max_length=20)
    total_sales: Amount = Field(gt=0)
    taxable_sales_vat: Amount = Field(default=Decimal("0"), ge=0)
    vat: Amount = Field(default=Decimal("0"), ge=0)
    excisable_amount: Amount = Field(default=Decimal("0"), ge=0)
    excise: Amount = Field(default=Decimal("0"), ge=0)
    taxable_sales_hst: Amount = Field(default=Decimal("0"), ge=0)
    hst: Amount = Field(default=Decimal("0"), ge=0)
    amount_for_esf: Amount = Field(default=Decimal("0"), ge=0)
    esf: Amount = Field(default=Decimal("0"), ge=0)
    export_sales: Amount = Field(default=Decimal("0"), ge=0)
    tax_exempted_sales: Amount = Field(default=Decimal("0"), ge=0)
    ref_invoice_number: str | None = Field(default=None, max_length=50)
    credit_note_number: str | None = Field(default=None, max_length=50)
    credit_note_date_bs: str | None = None
    reason_for_return: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _credit_note_fields(self) -> Self:
        if self.is_return:
            missing = [f for f in ("ref_invoice_number", "credit_note_number",
                                   "credit_note_date_bs", "reason_for_return")
                       if not getattr(self, f)]
            if missing:
                raise ValueError(f"credit notes require: {', '.join(missing)}")
        return self


class SyncStatusOut(ORM):
    status: str
    attempts: int
    last_response_code: int | None
    last_error: str | None
    synced_at: dt.datetime | None

    @computed_field
    @property
    def last_error_ne(self) -> str | None:
        return messages.to_nepali(self.last_error)


class InvoiceOut(ORM):
    id: uuid.UUID
    is_return: bool
    invoice_number: str
    invoice_date_bs: str
    invoice_date_ad: dt.date
    fiscal_year: str
    buyer_name: str
    buyer_pan: str | None
    total_sales: Amount
    taxable_sales_vat: Amount
    vat: Amount
    excisable_amount: Amount
    excise: Amount
    taxable_sales_hst: Amount
    hst: Amount
    amount_for_esf: Amount
    esf: Amount
    export_sales: Amount
    tax_exempted_sales: Amount
    ref_invoice_number: str | None
    credit_note_number: str | None
    credit_note_date_bs: str | None
    reason_for_return: str | None
    created_at: dt.datetime
    sync: SyncStatusOut | None = None
