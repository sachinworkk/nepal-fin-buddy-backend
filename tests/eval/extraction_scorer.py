"""Deterministic extraction scorer: compares extracted fields against ground truth.

No LLM anywhere — every comparison is a rule. Field types have different rules:

- amount: Decimal compare with tolerance (default 0.01 = 1 paisa)
- date_bs: normalise separators (2083.05.02 == 2083-05-02 == 2083/05/02), then exact
- date_ad: ISO date compare
- pan: digits-only exact compare (tolerates stray spaces/dashes)
- text: case/space/punctuation-light compare ("Bhatbhateni Supermarket Pvt. Ltd."
  matches "bhatbhateni supermarket")
- optional fields: an extracted None matches an empty ground truth; a wrong
  non-empty value is scored as a miss.

Return shape per field: {"status": "correct"|"miss"|"missing", "expected", "actual"}.
"""

import datetime as dt
import re
from decimal import Decimal, InvalidOperation

AMOUNT_TOLERANCE = Decimal("0.01")

# fields -> comparison type; unknown fields fall back to "text"
FIELD_TYPES = {
    "vendor_name": "text",
    "vendor_pan": "pan",
    "bill_number": "text",
    "bill_date_bs": "date_bs",
    "bill_date_ad": "date_ad",
    "subtotal": "amount",
    "discount": "amount",
    "taxable_amount": "amount",
    "vat_amount": "amount",
    "total_amount": "amount",
}

_RE_SEPS = re.compile(r"[./-]")


def _amount(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value).replace(",", "").replace("Rs.", "").strip())
    except InvalidOperation:
        return None


def _bs(value) -> str | None:
    if value is None or value == "":
        return None
    parts = _RE_SEPS.split(str(value).strip())
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return str(value)
    return "-".join(parts)


def _ad(value) -> str | None:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, dt.date):
            return value.isoformat()
        return dt.date.fromisoformat(str(value).strip()).isoformat()
    except ValueError:
        return str(value)


def _pan(value) -> str | None:
    if value is None or value == "":
        return None
    digits = re.sub(r"\D", "", str(value))
    return digits or None


def _text(value) -> str | None:
    if value is None or value == "":
        return None
    s = re.sub(r"[.,()\-]", " ", str(value).lower())
    return " ".join(s.split()) or None


def _normalise(field: str, value):
    t = FIELD_TYPES.get(field, "text")
    if t == "amount":
        return _amount(value)
    if t == "date_bs":
        return _bs(value)
    if t == "date_ad":
        return _ad(value)
    if t == "pan":
        return _pan(value)
    return _text(value)


def _text_match(norm: str | None, exp: str | None) -> bool:
    if norm == exp:
        return True
    # containment: labels often carry legal suffixes the extractor omits
    return bool(norm and exp and (norm in exp or exp in norm))


def score_field(field: str, expected, actual) -> dict:
    norm = _normalise(field, actual)
    exp = _normalise(field, expected)
    if exp is None:
        return {"status": "correct" if norm is None else "miss",
                "expected": expected, "actual": actual}
    if norm is None:
        return {"status": "missing", "expected": expected, "actual": actual}
    if FIELD_TYPES.get(field) == "amount" and isinstance(norm, Decimal) \
            and isinstance(exp, Decimal):
        ok = abs(norm - exp) <= AMOUNT_TOLERANCE
    elif FIELD_TYPES.get(field, "text") == "text" and isinstance(norm, str) \
            and isinstance(exp, str):
        ok = _text_match(norm, exp)
    else:
        ok = norm == exp
    return {"status": "correct" if ok else "miss",
            "expected": expected, "actual": actual}


def score_extraction(expected: dict, actual: dict) -> dict:
    """Score every expected field. Returns per-field results + counts."""
    results = {}
    for field, exp in expected.items():
        results[field] = score_field(field, exp, actual.get(field))
    correct = sum(1 for r in results.values() if r["status"] == "correct")
    return {
        "fields": results,
        "correct": correct,
        "total": len(results),
        "accuracy": correct / len(results) if results else 0.0,
    }


def accuracy_by_field(runs: list[dict]) -> dict[str, dict]:
    """Aggregate score_extraction() results across many bills.

    Returns {field: {"correct": n, "total": n, "accuracy": f, "misses": [bill ids]}}.
    """
    agg: dict[str, dict] = {}
    for run in runs:
        for field, res in run["fields"].items():
            a = agg.setdefault(field, {"correct": 0, "total": 0, "misses": []})
            a["total"] += 1
            if res["status"] == "correct":
                a["correct"] += 1
            else:
                a["misses"].append((run.get("bill_id"), res["expected"], res["actual"]))
    for a in agg.values():
        a["accuracy"] = a["correct"] / a["total"] if a["total"] else 0.0
    return agg


def format_report(by_field: dict[str, dict]) -> str:
    """Plain-text per-field accuracy table, sorted worst first."""
    lines = ["field               correct/total  accuracy   failing bills"]
    rows = sorted(by_field.items(), key=lambda kv: (kv[1]["accuracy"], kv[0]))
    for field, a in rows:
        ids = ", ".join(i for i, _, _ in a["misses"] if i) or "-"
        lines.append(f"{field:<20}{a['correct']:>4}/{a['total']:<4}"
                     f"  {a['accuracy']:>7.1%}   {ids}")
    return "\n".join(lines)