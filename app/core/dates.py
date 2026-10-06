"""BS <-> AD helpers. Dates are stored as AD internally; the original BS string is kept."""

import datetime as dt
import re

import nepali_datetime

_DEVANAGARI = str.maketrans("०१२३४५६७८९", "0123456789")
_DATE_RE = re.compile(r"(\d{4})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})")
_FIRST_BS_YEAR, _LAST_BS_YEAR = 1975, 2099


def to_ascii_digits(text: str) -> str:
    return text.translate(_DEVANAGARI)


def bs_to_ad(year: int, month: int, day: int) -> dt.date:
    return nepali_datetime.date(year, month, day).to_datetime_date()


def ad_to_bs(d: dt.date) -> nepali_datetime.date:
    return nepali_datetime.date.from_datetime_date(d)


def format_bs(year: int, month: int, day: int) -> str:
    return f"{year:04d}.{month:02d}.{day:02d}"


def parse_bs_string(text: str) -> tuple[int, int, int]:
    """Parse '2074.07.06', '2074/7/6', '२०७४-०७-०६' (Y-M-D). Raises ValueError if invalid."""
    m = _DATE_RE.search(to_ascii_digits(text))
    if not m:
        raise ValueError(f"not a Y-M-D date: {text!r}")
    y, mo, d = (int(g) for g in m.groups())
    nepali_datetime.date(y, mo, d)  # validates against the BS calendar
    return y, mo, d


def looks_like_bs(y: int) -> bool:
    return _FIRST_BS_YEAR <= y <= _LAST_BS_YEAR and y > 2040


def parse_date_any(text: str) -> tuple[dt.date, str | None]:
    """Parse a bill date that may be BS or AD. Returns (ad_date, bs_raw_or_None).

    Year >= 2040 is treated as BS (AD years of interest are < 2040). bs_raw is the
    normalised BS string when the input was BS, else None.
    """
    m = _DATE_RE.search(to_ascii_digits(text))
    if not m:
        raise ValueError(f"unparseable date: {text!r}")
    y, mo, d = (int(g) for g in m.groups())
    if looks_like_bs(y):
        return bs_to_ad(y, mo, d), format_bs(y, mo, d)
    return dt.date(y, mo, d), None



