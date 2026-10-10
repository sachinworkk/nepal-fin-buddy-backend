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
    entry_deadline: dt.date = Field(description="Last day to enter the bill (end of its period)")
    draw_date: dt.date = Field(description="Date of the IRD draw the bill goes into")
    days_left: int = Field(description="Days until entry_deadline (0 = last day)")


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



class PrizeWinOut(BaseModel):
    """One of the user's submitted coupons that appears in a published draw."""

    prize_coupon_number: str
    enrollment_id: uuid.UUID
    bill_number: str
    bill_date: dt.date
    draw_id: str
    draw_title_en: str
    draw_title_ne: str | None
    category_title_en: str
    category_title_ne: str | None
    winner_rank: int
    published_at: dt.datetime | None
    claim_deadline: dt.datetime | None
    claim_open: bool


class PrizeBumperDrawOut(BaseModel):
    """The most recent published draw in one prize category (bumper or daily) of the fortnightly draw."""

    draw_id: str
    title_en: str
    title_ne: str | None
    eligible_from: dt.date | None
    eligible_to: dt.date | None
    published_at: dt.datetime | None
    claim_deadline: dt.datetime | None
    claim_open: bool
    winner_count: int


class PrizeSummaryOut(BaseModel):
    """IRD lottery card: the current draw period and the user's standing.

    IRD draws per BS half-month: bills dated the 1st-15th must be entered by the 15th and go
    into the draw on the 16th; bills dated the 16th-month end must be entered by the month
    end and go into the draw on the 1st of the next month. A bill can't be entered in a later
    period. Each draw has a bumper prize (whole period) and daily prizes (by purchase date).
    """

    fiscal_year_code: str | None = None
    fiscal_year_name: str | None = None
    period_start: dt.date | None = None
    entry_deadline: dt.date | None = Field(
        default=None, description="Last day to enter bills of the current draw period")
    draw_date: dt.date | None = None
    days_left: int | None = None
    coupons_this_year: int = 0
    coupons_all_time: int = 0
    eligible_this_year: int = Field(
        default=0, description="Bills still enterable (in the current period), not yet entered")
    pending_handoffs: int = 0
    latest_bumper: PrizeBumperDrawOut | None = None
    latest_daily: PrizeBumperDrawOut | None = None
    bumper_prize_amount_npr: Decimal | None = Field(
        default=None, description="Display-only; configured, the portal doesn't publish it")
