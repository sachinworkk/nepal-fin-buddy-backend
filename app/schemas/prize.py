import datetime as dt
import uuid
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PrizeConfigOut(BaseModel):
    turnstile_sitekey: str
    payment_methods: list[str]
    # "direct": POST /prize/enrollments submits to the portal (test mock only).
    # "handoff": prefill via /prize/enrollments/handoff, user submits on the IRD portal.
    submission_mode: Literal["direct", "handoff"]


class PrizeEnrollmentIn(BaseModel):
    """Bill details are prefilled server-side from the confirmed bill `bill_id`."""

    bill_id: uuid.UUID
    payment_method: str = "Cash"
    name: str = Field(min_length=2, max_length=120)
    address: str = Field(min_length=1, max_length=200)
    mobile_number: str = Field(min_length=10, max_length=10)
    company_website: str | None = Field(default=None, max_length=200)
    # Frontend solves Cloudflare Turnstile and records when the form was opened.
    turnstile_token: str = Field(min_length=1, max_length=2048)
    form_started_at: int = Field(description="Epoch ms when the frontend form was opened")


class PrizeEnrollmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    bill_id: uuid.UUID | None
    seller_pan_no: str
    bill_number: str
    bill_date: dt.date
    billed_total_amount: Decimal
    payment_method: str
    name: str
    address: str
    mobile_number: str
    company_website: str | None
    status: str
    prize_coupon_number: str | None
    verification_id: str | None
    duplicate: bool
    last_error: str | None
    created_at: dt.datetime

class PrizeEligibleOut(BaseModel):
    """A confirmed bill that has every field the portal needs and no live enrollment."""

    bill_id: uuid.UUID
    vendor_name: str | None
    seller_pan_no: str
    bill_number: str
    bill_date: dt.date
    billed_total_amount: Decimal
    # From the portal's fiscal-year list; null when the portal is unreachable or no year matches.
    fiscal_year_code: str | None
    fiscal_year_status: str | None
    days_left: int | None = Field(description="Days until the fiscal year ends (ACTIVE years only)")


class PrizeHandoffIn(BaseModel):
    """Same prefill rules as PrizeEnrollmentIn, minus the Turnstile bits."""

    bill_id: uuid.UUID
    payment_method: str = "Cash"
    name: str = Field(min_length=2, max_length=120)
    address: str = Field(min_length=1, max_length=200)
    mobile_number: str = Field(min_length=10, max_length=10)
    company_website: str | None = Field(default=None, max_length=200)


class PrizeHandoffOut(BaseModel):
    enrollment_id: uuid.UUID
    fields: dict[str, str]
    portal_url: str


class PrizeEnrollmentPatchIn(BaseModel):
    """"submitted": the user finished the portal form. "dismissed": they won't submit it."""

    status: Literal["submitted", "dismissed"]
    prize_coupon_number: str | None = Field(default=None, min_length=1, max_length=12)
