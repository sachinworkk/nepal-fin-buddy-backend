"""Bill extraction: send a bill image/PDF to the vision LLM, get structured fields.

The model must answer with a single JSON object; we parse it defensively (strips
code fences, finds the outermost object). Dates may come back BS or AD — the
caller uses app.core.dates.parse_date_any to normalise.
"""

import base64
import json
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app import llm
from app.config import get_settings
from app.core.payment_methods import PaymentMethod
from app.llm import LlmError

log = structlog.get_logger()

SYSTEM_PROMPT = """You are a bill extraction engine for Nepali retail bills. \
You will be given an image (or PDF) of a bill the user received. Transcribe the \
structured data EXACTLY as printed. Respond with ONLY a JSON object — no prose, \
no code fences — with these keys:

{
  "vendor_name": string|null,
  "vendor_pan": string|null,          // digits only, the seller's PAN
  "bill_number": string|null,
  "bill_date_bs": string|null,        // exactly as printed, e.g. "2082.05.02" \
(Y-M-D in Bikram Sambat). Null if not a BS date.
  "bill_date_ad": string|null,        // ISO "YYYY-MM-DD" if printed; else null
  "subtotal": string|null,            // decimal strings like "6895.00", no \
currency symbols
  "discount": string|null,
  "taxable_amount": string|null,
  "vat_amount": string|null,
  "total_amount": string|null,
  "payment_method": "cash"|"card"|"qr"|"cheque"|null,  // how the bill was paid, \
only if printed (e.g. "Cash", "Visa", "eSewa"); else null
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
- If a field is not printed, use null. Do not guess."""

_KEYS = {
    "vendor_name", "vendor_pan", "bill_number", "bill_date_bs", "bill_date_ad",
    "subtotal", "discount", "taxable_amount", "vat_amount", "total_amount",
    "payment_method", "items", "ocr_text",
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
    "credit card": "card", "debit card": "card", "visa": "card", "mastercard": "card",
}


def _clean_payment(value) -> str | None:
    """Map the printed payment method onto a PaymentMethod value; None when unrecognised."""
    s = (_clean(value) or "").lower()
    s = _PAYMENT_ALIASES.get(s, s)
    return s if s in {m.value for m in PaymentMethod} else None


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
    out["items"] = _clean_items(raw.get("items"))
    out["ocr_text"] = _clean(raw.get("ocr_text"))
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
