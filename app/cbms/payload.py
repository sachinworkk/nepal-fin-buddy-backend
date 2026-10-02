"""IssuedInvoice -> CBMS /api/bill or /api/billreturn JSON body."""

import datetime as dt

from app.models import IssuedInvoice

NEPAL_TZ = dt.timezone(dt.timedelta(hours=5, minutes=45), "NPT")

_AMOUNT_FIELDS = (
    "total_sales", "taxable_sales_vat", "vat", "excisable_amount", "excise",
    "taxable_sales_hst", "hst", "amount_for_esf", "esf", "export_sales", "tax_exempted_sales",
)


def build_payload(
    inv: IssuedInvoice, *, username: str, password: str, seller_pan: str,
    realtime: bool, now: dt.datetime | None = None,
) -> dict:
    now = (now or dt.datetime.now(dt.UTC)).astimezone(NEPAL_TZ)
    body = {
        "username": username,
        "password": password,
        "seller_pan": seller_pan,
        "buyer_pan": inv.buyer_pan or "",
        "buyer_name": inv.buyer_name,
        "fiscal_year": inv.fiscal_year,
    }
    if inv.is_return:
        body |= {
            "ref_invoice_number": inv.ref_invoice_number,
            "credit_note_number": inv.credit_note_number,
            "credit_note_date": inv.credit_note_date_bs,
            "reason_for_return": inv.reason_for_return,
        }
    else:
        body |= {"invoice_number": inv.invoice_number, "invoice_date": inv.invoice_date_bs}
    body |= {f: float(getattr(inv, f)) for f in _AMOUNT_FIELDS}
    body |= {"isrealtime": realtime, "datetimeClient": now.isoformat(timespec="seconds")}
    return body
