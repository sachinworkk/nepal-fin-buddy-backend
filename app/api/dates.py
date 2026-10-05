import datetime as dt

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.api.deps import CurrentUser
from app.core import dates

router = APIRouter(prefix="/dates", tags=["dates"])


class DateConversion(BaseModel):
    ad: dt.date
    bs: str  # YYYY.MM.DD
    bs_label: str  # e.g. "19 Asoj 2082"


@router.get("/convert", response_model=DateConversion)
async def convert(user: CurrentUser, ad: dt.date | None = None, bs: str | None = None):
    """Convert BS -> AD or AD -> BS. Pass exactly one of `ad` / `bs`."""
    if (ad is None) == (bs is None):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Provide either ad or bs")
    try:
        if bs is not None:
            ad = dates.bs_to_ad(*dates.parse_bs_string(bs))
        b = dates.ad_to_bs(ad)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None
    return DateConversion(
        ad=ad, bs=dates.format_bs(b.year, b.month, b.day),
        bs_label=f"{b.day} {b.strftime('%B')} {b.year}",
    )
