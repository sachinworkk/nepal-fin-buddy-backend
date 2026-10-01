import datetime as dt
from decimal import Decimal

import pytest

from app.cbms import messages
from app.cbms.payload import build_payload
from app.models import IssuedInvoice
from app.worker.sync import backoff_seconds

AMOUNTS = {
    "total_sales": Decimal("1130.00"), "taxable_sales_vat": Decimal("1000.00"),
    "vat": Decimal("130.00"), "excisable_amount": Decimal("0"), "excise": Decimal("0"),
    "taxable_sales_hst": Decimal("0"), "hst": Decimal("0"), "amount_for_esf": Decimal("0"),
    "esf": Decimal("0"), "export_sales": Decimal("0"), "tax_exempted_sales": Decimal("0"),
}
NOW = dt.datetime(2024, 8, 26, 6, 15, tzinfo=dt.UTC)
CREDS = {"username": "Test_CBMS", "password": "test@321", "seller_pan": "999999999"}


def invoice(**kw) -> IssuedInvoice:
    return IssuedInvoice(**{
        "is_return": False, "invoice_number": "INV-1", "invoice_date_bs": "2081.05.10",
        "invoice_date_ad": dt.date(2024, 8, 26), "fiscal_year": "2081.082",
        "buyer_name": "Ram Traders", "buyer_pan": None, **AMOUNTS, **kw,
    })


def test_bill_payload():
    p = build_payload(invoice(), **CREDS, realtime=True, now=NOW)
    assert p == {
        **CREDS, "buyer_pan": "", "buyer_name": "Ram Traders", "fiscal_year": "2081.082",
        "invoice_number": "INV-1", "invoice_date": "2081.05.10",
        **{k: float(v) for k, v in AMOUNTS.items()},
        "isrealtime": True,
        "datetimeClient": "2024-08-26T12:00:00+05:45",  # Nepal time, ISO 8601
    }
    assert all(type(p[k]) is float for k in AMOUNTS)


def test_bill_return_payload():
    cn = invoice(is_return=True, invoice_number="CN-1", buyer_pan="123456789",
                 ref_invoice_number="INV-1", credit_note_number="CN-1",
                 credit_note_date_bs="2081.05.20", reason_for_return="Damaged goods")
    p = build_payload(cn, **CREDS, realtime=False, now=NOW)
    assert p["ref_invoice_number"] == "INV-1" and p["credit_note_number"] == "CN-1"
    assert p["credit_note_date"] == "2081.05.20" and p["reason_for_return"] == "Damaged goods"
    assert p["buyer_pan"] == "123456789" and p["isrealtime"] is False
    assert "invoice_number" not in p and "invoice_date" not in p


@pytest.mark.parametrize(("code", "status"), [
    (200, "synced"), (101, "synced"), (100, "failed"), (102, "failed"), (104, "failed"),
    (103, "retry"), (105, "retry"), (999, "retry"),
])
def test_code_outcomes(code, status):
    assert messages.for_code(code).status == status


def test_every_message_has_nepali():
    for o in (*messages.CODES.values(), messages.UNREACHABLE, messages.NO_CREDENTIALS,
              messages.GAVE_UP, messages.WAITING_FOR_ORIGINAL):
        assert o.en and o.ne
        assert messages.to_nepali(o.en) == o.ne
    assert messages.to_nepali(None) is None


def test_backoff_grows_with_jitter_and_caps():
    for attempt, full in ((1, 30), (2, 60), (3, 120)):
        d = backoff_seconds(attempt, 30, 3600)
        assert full / 2 <= d <= full
    assert backoff_seconds(20, 30, 3600) <= 3600
    assert backoff_seconds(3, 0, 3600) == 0
