import datetime as dt
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict

from app.core import dates


def _two_dp(v: Decimal) -> Decimal:
    if v.as_tuple().exponent < -2:
        raise ValueError("at most 2 decimal places")
    return v


Amount = Annotated[Decimal, AfterValidator(_two_dp)]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def resolve_date(ad: dt.date | None, bs: str | None) -> tuple[dt.date, str]:
    """Resolve an AD date or a BS string to (ad_date, normalised_bs_string)."""
    if ad is not None and bs is not None:
        raise ValueError("provide either the AD date or the BS date, not both")
    if bs is not None:
        y, m, d = dates.parse_bs_string(bs)
        return dates.bs_to_ad(y, m, d), dates.format_bs(y, m, d)
    if ad is None:
        raise ValueError("a date is required")
    b = dates.ad_to_bs(ad)
    return ad, dates.format_bs(b.year, b.month, b.day)
