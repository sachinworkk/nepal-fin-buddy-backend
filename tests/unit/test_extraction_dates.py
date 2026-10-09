import json

from app.extraction import parse_extraction


def _parse(**fields) -> dict:
    return parse_extraction(json.dumps(fields))


def test_bs_date_in_ad_slot_is_moved():
    out = _parse(bill_date_ad="2083-06-06", bill_date_bs="09/22/26")
    assert out["bill_date_bs"] == "2083-06-06"
    assert out["bill_date_ad"] == "2026-09-22"


def test_bs_date_only_in_ad_slot():
    out = _parse(bill_date_ad="2079/11/12")
    assert out["bill_date_bs"] == "2079/11/12"
    assert out["bill_date_ad"] is None


def test_correct_dates_untouched():
    out = _parse(bill_date_ad="2023-02-24", bill_date_bs="2079.11.12")
    assert (out["bill_date_bs"], out["bill_date_ad"]) == ("2079.11.12", "2023-02-24")


def test_ad_date_in_bs_slot_is_moved():
    out = _parse(bill_date_bs="24/02/2023")
    assert out["bill_date_bs"] is None
    assert out["bill_date_ad"] == "2023-02-24"
