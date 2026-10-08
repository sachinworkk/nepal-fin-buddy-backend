"""Tests for the deterministic extraction scorer (no LLM, runs in default suite)."""

from decimal import Decimal

from tests.eval.extraction_scorer import (
    accuracy_by_field,
    format_report,
    score_extraction,
    score_field,
)


def test_amount_exact_and_tolerance():
    assert score_field("total_amount", "1130.00", 1130)["status"] == "correct"
    assert score_field("total_amount", "1130.00", "Rs. 1,130.01")["status"] == "correct"
    assert score_field("total_amount", "1130.00", "1130.02")["status"] == "miss"


def test_amount_garbage_is_missing():
    # unparseable output counts as "field missing", not a wrong value
    r = score_field("vat_amount", "130.00", "see receipt")
    assert r["status"] == "missing"


def test_bs_date_separator_normalisation():
    for fmt in ("2083.05.02", "2083/05/02", "2083-05-02"):
        assert score_field("bill_date_bs", "2083-05-02", fmt)["status"] == "correct"


def test_ad_date():
    assert score_field("bill_date_ad", "2026-08-18", "2026-08-18")["status"] == "correct"
    assert score_field("bill_date_ad", "2026-08-18", "18/08/2026")["status"] == "miss"


def test_pan_digits_only():
    assert score_field("vendor_pan", "601234567", "601-234-567")["status"] == "correct"
    assert score_field("vendor_pan", "601234567", "601234568")["status"] == "miss"


def test_text_normalisation():
    assert score_field("vendor_name", "Bhatbhateni Supermarket Pvt. Ltd.",
                       "bhatbhateni supermarket")["status"] == "correct"
    assert score_field("vendor_name", "Bhatbhateni", "BigMart")["status"] == "miss"


def test_missing_and_none_semantics():
    # both empty -> correct (optional field, nothing to get wrong)
    assert score_field("discount", None, None)["status"] == "correct"
    # ground truth is empty; extractor also empty -> correct
    assert score_field("discount", "", "")["status"] == "correct"
    # ground truth empty but extractor invented a value -> miss
    assert score_field("discount", None, "50")["status"] == "miss"
    # ground truth present, extractor None -> missing
    assert score_field("vendor_pan", "601234567", None)["status"] == "missing"


def test_score_extraction_whole_bill():
    expected = {
        "vendor_name": "Bhatbhateni", "vendor_pan": "601234567",
        "total_amount": "7791.35", "vat_amount": "896.35",
        "bill_date_bs": "2083-05-02", "bill_date_ad": "2026-08-18",
    }
    actual = {
        "vendor_name": "Bhatbhateni Supermarket", "vendor_pan": "601234567",
        "total_amount": Decimal("7791.35"), "vat_amount": "900.00",  # vat wrong
        "bill_date_bs": "2083.05.02", "bill_date_ad": "2026-08-18",
    }
    r = score_extraction(expected, actual)
    assert r["correct"] == 5 and r["total"] == 6
    assert abs(r["accuracy"] - 5 / 6) < 1e-9
    assert r["fields"]["vat_amount"]["status"] == "miss"


def test_accuracy_by_field_aggregates():
    expected = {"total_amount": "100.00", "vat_amount": "13.00", "vendor_pan": "123"}
    runs = [
        {"bill_id": "b1", **score_extraction(expected, {"total_amount": "100", "vat_amount": "13", "vendor_pan": "123"})},
        {"bill_id": "b2", **score_extraction(expected, {"total_amount": "101", "vat_amount": None, "vendor_pan": "123"})},
    ]
    agg = accuracy_by_field(runs)
    assert agg["total_amount"]["accuracy"] == 0.5
    assert agg["vat_amount"]["accuracy"] == 0.5
    assert agg["vendor_pan"]["accuracy"] == 1.0
    assert agg["total_amount"]["misses"][0][0] == "b2"


def test_format_report_orders_worst_first():
    runs = [
        {"bill_id": "b1", **score_extraction({"total_amount": "1", "vat_amount": "1"},
                                             {"total_amount": "1", "vat_amount": "2"})},
        {"bill_id": "b2", **score_extraction({"total_amount": "1", "vat_amount": "1"},
                                             {"total_amount": "2", "vat_amount": "1"})},
    ]
    report = format_report(accuracy_by_field(runs))
    lines = report.splitlines()
    assert lines[0].startswith("field")
    # both 50%; tie broken alphabetically
    assert "total_amount" in lines[1] and "vat_amount" in lines[2]
    assert "50.0%" in report

def test_payment_method_enum():
    assert score_field("payment_method", "cash", "Cash")["status"] == "correct"
    assert score_field("payment_method", "cash", "card")["status"] == "miss"
    assert score_field("payment_method", None, None)["status"] == "correct"
    assert score_field("payment_method", None, "qr")["status"] == "miss"
    assert score_field("payment_method", "cash", None)["status"] == "missing"


def test_items_order_insensitive_and_strict_on_count():
    exp = [{"description": "Momo Platter", "amount": "756.63"},
           {"description": "Water bottle", "amount": "48.67"}]
    got = [{"description": "water bottle", "amount": "48.67"},
           {"description": "Momo Platter", "amount": "756.63"}]
    assert score_field("items", exp, got)["status"] == "correct"
    assert score_field("items", exp, got[:1])["status"] == "miss"
    assert score_field("items", exp, got + [{"description": "x", "amount": "1"}])["status"] == "miss"
    assert score_field("items", exp, [{"description": "Momo Platter", "amount": "700"},
                                      got[0]])["status"] == "miss"
    assert score_field("items", exp, None)["status"] == "missing"
    assert score_field("items", [], None)["status"] == "correct"
