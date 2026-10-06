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
