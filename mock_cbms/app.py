"""Mock of IRD's CBMS /api/bill and /api/billreturn, for development and tests.

Like the real service it answers with a bare integer code:
  200 saved, 100 bad credentials, 101 already exists, 102 could not save (invalid payload),
  103 unknown error, 104 model invalid, 105 (billreturn) referenced bill does not exist.
Magic buyer names force a code so every path can be exercised:
  FORCE_103 / FORCE_104  -> always that code
  FLAKY_103              -> 103 on the first submission of a bill, then normal handling
State is in memory and resets on restart.
"""

import os
import re
from typing import Annotated

from fastapi import Body, FastAPI
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

_PAN = re.compile(r"^\d{9}$")
_FY = re.compile(r"^\d{4}\.\d{3}$")
_BS_DATE = re.compile(r"^\d{4}\.\d{2}\.\d{2}$")
Amount = Annotated[float, Field(ge=0)]


class _Common(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str
    password: str
    seller_pan: str
    buyer_pan: str = ""
    buyer_name: str
    fiscal_year: str = Field(pattern=_FY.pattern)
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
    isrealtime: bool
    datetimeClient: str

    @field_validator("seller_pan")
    @classmethod
    def _seller_pan(cls, v: str) -> str:
        if not _PAN.match(v):
            raise ValueError("PAN must be 9 digits")
        return v

    @field_validator("buyer_pan")
    @classmethod
    def _buyer_pan(cls, v: str) -> str:
        if v and not _PAN.match(v):
            raise ValueError("PAN must be 9 digits")
        return v


class BillIn(_Common):
    invoice_number: str = Field(min_length=1)
    invoice_date: str = Field(pattern=_BS_DATE.pattern)


class BillReturnIn(_Common):
    ref_invoice_number: str = Field(min_length=1)
    credit_note_number: str = Field(min_length=1)
    credit_note_date: str = Field(pattern=_BS_DATE.pattern)
    reason_for_return: str = Field(min_length=1)


def create_app() -> FastAPI:
    app = FastAPI(title="Mock CBMS")
    sandbox = (os.getenv("CBMS_SANDBOX_USERNAME", "Test_CBMS"),
               os.getenv("CBMS_SANDBOX_PASSWORD", "test@321"))
    saved: set[tuple] = set()
    flaky_seen: set[tuple] = set()

    def handle(kind: str, model: type[_Common], raw: dict) -> int:
        try:
            b = model.model_validate(raw)
        except ValidationError:
            return 102
        if (b.username, b.password) != sandbox:
            return 100
        number = b.invoice_number if isinstance(b, BillIn) else b.credit_note_number
        key = (kind, b.seller_pan, b.fiscal_year, number)
        if b.buyer_name == "FORCE_103":
            return 103
        if b.buyer_name == "FORCE_104":
            return 104
        if b.buyer_name == "FLAKY_103" and key not in flaky_seen:
            flaky_seen.add(key)
            return 103
        if key in saved:
            return 101
        if isinstance(b, BillReturnIn) and not any(
            k == ("bill", b.seller_pan, b.fiscal_year, b.ref_invoice_number)
            for k in saved
        ):
            return 105
        saved.add(key)
        return 200

    @app.post("/api/bill")
    async def bill(raw: Annotated[dict, Body()]) -> int:
        return handle("bill", BillIn, raw)

    @app.post("/api/billreturn")
    async def bill_return(raw: Annotated[dict, Body()]) -> int:
        return handle("billreturn", BillReturnIn, raw)

    return app


app = create_app()
