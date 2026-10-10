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


def test_invented_bs_date_is_dropped_and_ad_recovered_from_transcription():
    # A receipt that prints only "Bill Date: 03/Oct/2026"; the model made up a BS date.
    out = _parse(bill_date_bs="2082.05.02", bill_date_ad=None,
                 ocr_text="BIG MART\nBill Date : 03/Oct/2026\nBill No: 414/CM/00022090/Oct/26")
    assert out["bill_date_bs"] is None
    assert out["bill_date_ad"] == "2026-10-03"


def test_bs_date_printed_on_the_bill_is_kept():
    out = _parse(bill_date_bs="2082.05.02", ocr_text="Miti: 2082 Bhadra 2\nTotal 6700")
    assert out["bill_date_bs"] == "2082.05.02"


def test_existing_ad_date_is_not_overwritten_by_transcription():
    out = _parse(bill_date_ad="2026-10-03", ocr_text="Bill Date: 04/Oct/2026")
    assert out["bill_date_ad"] == "2026-10-03"
