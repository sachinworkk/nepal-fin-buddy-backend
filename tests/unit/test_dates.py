import datetime as dt

import pytest

from app.core import dates


def test_bs_to_ad_known_pair():
    assert dates.bs_to_ad(2074, 7, 6) == dt.date(2017, 10, 23)


def test_roundtrip_many_days():
    d = dt.date(2020, 1, 1)
    for _ in range(2000):
        b = dates.ad_to_bs(d)
        assert dates.bs_to_ad(b.year, b.month, b.day) == d
        d += dt.timedelta(days=1)


@pytest.mark.parametrize("s", ["2074.07.06", "2074/7/6", "२०७४-०७-०६", "on 2074 . 07 . 06 ok"])
def test_parse_bs_string_variants(s):
    assert dates.parse_bs_string(s) == (2074, 7, 6)


def test_parse_bs_string_rejects_invalid_day():
    with pytest.raises(ValueError):
        dates.parse_bs_string("2074.07.40")


def test_parse_bs_string_rejects_garbage():
    with pytest.raises(ValueError):
        dates.parse_bs_string("yesterday")


def test_parse_date_any_bs_and_ad():
    assert dates.parse_date_any("2074.07.06") == (dt.date(2017, 10, 23), "2074.07.06")
    assert dates.parse_date_any("2017-10-23") == (dt.date(2017, 10, 23), None)


def test_devanagari_digits():
    assert dates.to_ascii_digits("रू. १,२३४.५०") == "रू. 1,234.50"


@pytest.mark.parametrize("bs, start, end, draw", [
    ((2083, 6, 1), (2083, 6, 1), (2083, 6, 15), (2083, 6, 16)),
    ((2083, 6, 15), (2083, 6, 1), (2083, 6, 15), (2083, 6, 16)),
    ((2083, 6, 16), (2083, 6, 16), (2083, 6, 31), (2083, 7, 1)),
    ((2083, 6, 31), (2083, 6, 16), (2083, 6, 31), (2083, 7, 1)),
    ((2083, 3, 32), (2083, 3, 16), (2083, 3, 32), (2083, 4, 1)),      # 32-day month
    ((2082, 12, 20), (2082, 12, 16), (2082, 12, 30), (2083, 1, 1)),   # Chaitra -> Baisakh
])
def test_draw_period(bs, start, end, draw):
    p = dates.draw_period(dates.bs_to_ad(*bs))
    assert p == (dates.bs_to_ad(*start), dates.bs_to_ad(*end), dates.bs_to_ad(*draw))
