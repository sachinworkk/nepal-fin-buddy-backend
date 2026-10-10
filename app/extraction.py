"""Bill extraction: send a bill image/PDF to the vision LLM, get structured fields.

The model must answer with a single JSON object; we parse it defensively (strips
code fences, finds the outermost object). Dates may come back BS or AD — the
caller uses app.core.dates.parse_date_any to normalise.
"""

import base64
import datetime as dt
import json
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app import llm
from app.config import get_settings
from app.core.categories import Category
from app.core.dates import looks_like_bs, to_ascii_digits
from app.core.payment_methods import PaymentMethod
from app.llm import LlmError

log = structlog.get_logger()

SYSTEM_PROMPT = """You are a bill extraction engine for Nepali retail bills. \
You will be given an image (or PDF) of a bill the user received. Transcribe the \
structured data EXACTLY as printed. Respond with ONLY a JSON object — no prose, \
no code fences — with these keys:

{
  "vendor_name": string|null,
  "vendor_pan": string|null,          // digits only, the seller's PAN or VAT \
number; never a DDA, licence or registration number
  "bill_number": string|null,
  "bill_date_bs": string|null,        // exactly as printed (Y-M-D in Bikram \
Sambat). Null unless a BS date is actually printed on the bill.
  "bill_date_ad": string|null,        // ISO "YYYY-MM-DD" if printed; else null
  "subtotal": string|null,            // decimal strings like "6895.00", no \
currency symbols
  "discount": string|null,
  "taxable_amount": string|null,
  "vat_amount": string|null,
  "total_amount": string|null,
  "payment_method": "cash"|"card"|"qr"|"cheque"|null,  // how the bill was paid: \
printed words ("Cash", "Visa") or a payment logo/QR code on the bill (eSewa, Khalti, \
Fonepay, a bank QR -> "qr"; Visa/Mastercard -> "card"); null if there is no sign, \
or if several methods are listed as options (e.g. "Cash/Credit/Cheque") with none \
marked as the one used. Never infer it from the type of shop or bill: no printed \
word or logo means null
  "category": "groceries"|"dining"|"transport"|"utilities"|"rent"|"medical"|\
"education"|"shopping"|"entertainment"|"fees"|"gifts"|"other",  // your best guess \
from the vendor and items; "other" if unclear
  "items": [ { "description": string, "quantity": string|null,
               "unit_price": string|null, "amount": string|null } ],
  "ocr_text": string                  // your full plain-text transcription of \
the bill, top to bottom
}

Rules:
- VAT is 13% in Nepal; copy the printed numbers verbatim, never compute or \
"correct" them.
- Amounts: strip currency symbols (रू / Rs / NPR) and thousand separators, keep \
decimals.
- Dates: a Bikram Sambat (BS) date has a year of about 2070-2090 and is often labelled \
"Miti"/"Mitti"/"मिति"; put it in bill_date_bs. A Gregorian (AD) date has a year of \
about 2000-2030; put it in bill_date_ad as ISO YYYY-MM-DD (a second date \
printed beside a BS date, e.g. "(09/22/26)", is the AD date in month/day/year order: \
write it as "2026-09-22"; a date with a month name such as "03/Oct/2026" is AD, \
write it as "2026-10-03"). If both are printed, fill both. Never convert \
between the calendars yourself: a date that is not printed stays null, so a bill \
with only an AD date has bill_date_bs null.
- If a field is not printed, use null. Do not guess, except category (always infer one); \
payment_method only counts printed words or logos/QR."""

_KEYS = {
    "vendor_name", "vendor_pan", "bill_number", "bill_date_bs", "bill_date_ad",
    "subtotal", "discount", "taxable_amount", "vat_amount", "total_amount",
    "payment_method", "category", "items", "ocr_text",
}
_ITEM_KEYS = {"description", "quantity", "unit_price", "amount"}
_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)
_NUMERIC_FIELDS = {"subtotal", "discount", "taxable_amount", "vat_amount", "total_amount",
                   "quantity", "unit_price", "amount"}
_NUM_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def _clean(value) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    s = str(value).strip()
    return s or None


def _clean_num(value) -> str | None:
    """Coerce an amount/quantity to a decimal-safe string ('4x' -> '4', 'NPR 880' -> '880').

    Returns None when nothing numeric survives — the DB columns are NUMERIC and
    asyncpg rejects non-numeric strings outright.
    """
    s = _clean(value)
    if s is None:
        return None
    m = _NUM_RE.search(s.replace(",", ""))
    return m.group(0) if m else None


_PAYMENT_ALIASES = {
    "check": "cheque", "esewa": "qr", "khalti": "qr", "fonepay": "qr", "wallet": "qr",
    "connectips": "qr", "imepay": "qr", "qr": "qr",
    "credit card": "card", "debit card": "card", "visa": "card", "mastercard": "card",
}


def _clean_payment(value) -> str | None:
    """Map the printed payment method onto a PaymentMethod value; None when unrecognised."""
    s = (_clean(value) or "").lower()
    if s not in {m.value for m in PaymentMethod}:
        # "Paid via eSewa", "Fonepay QR": fall back to a substring match on the aliases.
        s = _PAYMENT_ALIASES.get(s) or next(
            (v for k, v in _PAYMENT_ALIASES.items() if k in s), s)
    return s if s in {m.value for m in PaymentMethod} else None


def _clean_category(value) -> str | None:
    """The model's category guess if it is a valid Category value, else None."""
    s = (_clean(value) or "").lower()
    return s if s in {c.value for c in Category} else None


def _clean_items(items) -> list[dict] | None:
    if not isinstance(items, list):
        return None
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        cleaned = {}
        for k, v in it.items():
            if k not in _ITEM_KEYS:
                continue
            cleaned[k] = _clean_num(v) if k in _NUMERIC_FIELDS else _clean(v)
        if cleaned.get("description"):
            out.append(cleaned)
    return out or None


_DMY_RE = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})$")


def _dmy_to_iso(text: str) -> str | None:
    """'09/22/26' or '24/02/2023' -> ISO date; the day/month order is inferred."""
    m = _DMY_RE.match(text.strip())
    if not m:
        return None
    a, b, y = int(m[1]), int(m[2]), int(m[3])
    y += 2000 if y < 100 else 0
    month, day = (a, b) if a <= 12 else (b, a)
    try:
        return dt.date(y, month, day).isoformat()
    except ValueError:
        return None


def _fix_dates(out: dict) -> None:
    """Repair a BS date the model put in the AD field, and an AD date left in the BS field."""
    bs, ad, stray = out.get("bill_date_bs"), out.get("bill_date_ad"), None
    bs_ok = bool(bs) and looks_like_bs_string(bs)
    if ad and looks_like_bs_string(ad) and not bs_ok:
        bs, ad, stray = ad, None, bs  # swapped: the old BS slot may hold the AD date
    elif bs and not bs_ok:
        bs, stray = None, bs
    if stray and not ad:
        ad = _dmy_to_iso(stray)
    out["bill_date_bs"], out["bill_date_ad"] = bs, ad


_MON = ("jan feb mar apr may jun jul aug sep oct nov dec").split()
_DMON_RE = re.compile(r"\b(\d{1,2})\s*[/.\-\s]\s*([A-Za-z]{3})[A-Za-z]*\s*[/.\-\s]\s*(\d{4})\b")


def _check_dates_against_text(out: dict, ocr_text: str | None) -> None:
    """Cross-check the dates against the model's own transcription of the bill.

    A BS date whose year never appears in the transcription was made up (e.g. copied from a
    prompt example), so drop it. A missing AD date is recovered from a "03/Oct/2026" style date.
    """
    if not ocr_text:
        return
    text = to_ascii_digits(ocr_text)
    bs = out.get("bill_date_bs")
    if bs:
        year = re.match(r"\s*(\d{4})", to_ascii_digits(bs))
        if year and year[1] not in text:
            out["bill_date_bs"] = None
    if not out.get("bill_date_ad"):
        for day, mon, year in _DMON_RE.findall(text):
            if mon.lower() in _MON:
                try:
                    out["bill_date_ad"] = dt.date(int(year), _MON.index(mon.lower()) + 1,
                                                  int(day)).isoformat()
                except ValueError:
                    continue
                break


def looks_like_bs_string(text: str) -> bool:
    m = re.match(r"\s*(\d{4})\D", to_ascii_digits(text))
    return bool(m) and looks_like_bs(int(m[1]))


def parse_extraction(text: str) -> dict:
    """Parse the model's JSON answer into a cleaned extraction dict."""
    m = _OBJ_RE.search(text)
    if not m:
        raise LlmError("extraction response contained no JSON object")
    try:
        raw = json.loads(m.group(0))
    except ValueError as e:
        raise LlmError(f"extraction JSON parse failed: {e}") from None
    if not isinstance(raw, dict):
        raise LlmError("extraction JSON was not an object")
    out: dict = {k: _clean(raw.get(k)) for k in _KEYS if k not in ("items", "ocr_text")}
    for k in ("subtotal", "discount", "taxable_amount", "vat_amount", "total_amount"):
        out[k] = _clean_num(out[k])
    out["payment_method"] = _clean_payment(out["payment_method"])
    out["category"] = _clean_category(out["category"])
    out["items"] = _clean_items(raw.get("items"))
    out["ocr_text"] = _clean(raw.get("ocr_text"))
    _fix_dates(out)
    _check_dates_against_text(out, out["ocr_text"])
    return out


async def extract_bill(session: AsyncSession, user_id, data: bytes, mime_type: str) -> dict:
    """Run one extraction against the configured vision model. Returns cleaned fields."""
    s = get_settings()
    data_url = f"data:{mime_type};base64,{base64.b64encode(data).decode()}"
    if mime_type == "application/pdf":
        # OpenRouter rejects PDFs sent as image_url; they need a "file" part.
        doc_part = {"type": "file", "file": {"filename": "bill.pdf", "file_data": data_url}}
    else:
        doc_part = {"type": "image_url", "image_url": {"url": data_url}}
    resp = await llm._post(session, "extraction", user_id, "/chat/completions", {
        "model": s.extraction_model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                doc_part,
                {"type": "text", "text": "Extract this bill as JSON."},
            ]},
        ],
    })
    try:
        answer: str | None = resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise LlmError("extraction response missing message content") from None
    return parse_extraction(answer or "")


# Penalty per flag; confidence = 1.0 minus the penalties of the flags raised.
FLAG_PENALTIES = {
    "missing_total": 0.30,
    "total_mismatch": 0.25,
    "items_sum_mismatch": 0.15,
    "vat_rate_mismatch": 0.15,
    "missing_date": 0.15,
    "missing_pan": 0.05,
}
_TOLERANCE = Decimal("1")  # NPR; printed bills round
_VAT_RATE = Decimal("0.13")


def _dec(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def bill_checks(bill: Mapping) -> tuple[float, list[str]]:
    """Consistency checks on bill values -> (confidence 0..1, flag codes).

    Pure function of the values, so it is re-run on the edited bill and flags clear
    as the user fixes fields. Keys read: vendor_pan, bill_date_ad, subtotal, discount,
    taxable_amount, vat_amount, total_amount, items (dicts with 'amount').
    """
    subtotal, discount = _dec(bill.get("subtotal")), _dec(bill.get("discount"))
    taxable, vat = _dec(bill.get("taxable_amount")), _dec(bill.get("vat_amount"))
    total = _dec(bill.get("total_amount"))
    flags: list[str] = []

    if total is None:
        flags.append("missing_total")
    elif subtotal is not None and abs(subtotal - (discount or 0) + (vat or 0) - total) > _TOLERANCE:
        flags.append("total_mismatch")

    amounts = [_dec(i.get("amount")) for i in bill.get("items") or []]
    if amounts and subtotal is not None and None not in amounts and (
        abs(sum(amounts) - subtotal) > _TOLERANCE
    ):
        flags.append("items_sum_mismatch")

    if taxable is not None and vat is not None and abs(taxable * _VAT_RATE - vat) > _TOLERANCE:
        flags.append("vat_rate_mismatch")
    if not bill.get("bill_date_ad"):
        flags.append("missing_date")
    if not bill.get("vendor_pan"):
        flags.append("missing_pan")

    confidence = max(0.0, 1.0 - sum(FLAG_PENALTIES[f] for f in flags))
    return round(confidence, 2), flags
