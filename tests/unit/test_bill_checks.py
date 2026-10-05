import datetime as dt

from app.extraction import bill_checks

GOOD = {
    "vendor_pan": "301234567", "bill_date_ad": dt.date(2025, 10, 5),
    "subtotal": "1000.00", "discount": "0", "taxable_amount": "1000.00",
    "vat_amount": "130.00", "total_amount": "1130.00",
    "items": [{"amount": "600.00"}, {"amount": "400.00"}],
}


def test_clean_bill_has_full_confidence():
    assert bill_checks(GOOD) == (1.0, [])


def test_missing_total():
    conf, flags = bill_checks({**GOOD, "total_amount": None})
    assert flags == ["missing_total"] and conf == 0.7


def test_total_mismatch():
    conf, flags = bill_checks({**GOOD, "total_amount": "1500.00"})
    assert flags == ["total_mismatch"] and conf == 0.75


def test_total_within_rounding_tolerance():
    assert bill_checks({**GOOD, "total_amount": "1130.60"})[1] == []


def test_items_sum_mismatch():
    conf, flags = bill_checks({**GOOD, "items": [{"amount": "600.00"}, {"amount": "100.00"}]})
    assert flags == ["items_sum_mismatch"] and conf == 0.85


def test_items_with_unknown_amount_are_not_checked():
    assert bill_checks({**GOOD, "items": [{"amount": "600.00"}, {"amount": None}]})[1] == []


def test_vat_rate_mismatch():
    conf, flags = bill_checks({**GOOD, "vat_amount": "100.00", "total_amount": "1100.00"})
    assert flags == ["vat_rate_mismatch"] and conf == 0.85


def test_missing_date_and_pan():
    conf, flags = bill_checks({**GOOD, "bill_date_ad": None, "vendor_pan": None})
    assert flags == ["missing_date", "missing_pan"] and conf == 0.8


def test_empty_bill_confidence_floors_at_zero_or_above():
    conf, flags = bill_checks({})
    assert set(flags) == {"missing_total", "missing_date", "missing_pan"}
    assert 0 <= conf < 0.6
