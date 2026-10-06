"""Mock of the IRD prize portal's public API (prize.ird.gov.np/api/v1), for tests.

Mirrors the behaviour observed on the real portal:
  - GET  /api/v1/public/fiscal-years
  - POST /api/v1/public/enrollments (multipart, the cash-bill lottery entry)
Validation ladder order, error strings and the "form expired" time window match
the live service. Tokens must be non-empty; anything starting with "TEST_"
passes (tests). State (coupon counter, duplicates) is in memory and resets on
restart.
"""

import datetime as dt
import re
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

_FISCAL_YEARS = {
    "fiscal_years": [
        {"fiscal_year_code": "2083-84", "display_name": "FY 2083/84",
         "start_date": "2026-07-17", "end_date": "2027-07-16", "status": "ACTIVE"},
        {"fiscal_year_code": "2082-83", "display_name": "FY 2082/83",
         "start_date": "2025-07-17", "end_date": "2026-07-16", "status": "CLOSED"},
    ],
}

_BILL_NUMBER = re.compile(r"^[A-Za-z0-9/._-]{1,40}$")
_PAN = re.compile(r"^\d{9}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

FORM_EXPIRED = {"error": "enrollment form expired, please reload and try again"}
TURNSTILE_FAILED = {
    "error": "security verification failed or expired; please complete it again",
}


def create_app() -> FastAPI:
    app = FastAPI(title="Mock IRD Prize Portal")
    state = {"coupons": 0, "vids": 0, "seen": {}}

    @app.get("/api/v1/public/fiscal-years")
    async def fiscal_years() -> dict:
        return _FISCAL_YEARS

    @app.post("/api/v1/public/enrollments")
    async def enrollments(request: Request) -> JSONResponse:
        form = await request.form()
        f = {k: str(form.get(k) or "") for k in form}

        started_at = _int(f.get("form_started_at"))
        age = time.time() - started_at / 1000 if started_at else 1e12
        if age < 5 or age > 86400:
            return JSONResponse(FORM_EXPIRED, status_code=400)
        if not _BILL_NUMBER.fullmatch(f.get("bill_number", "")):
            return JSONResponse({"error": "bill number must be 1 to 40 letters, digits, "
                                          "slash, dot, dash, or underscore"}, status_code=400)
        if not _PAN.fullmatch(f.get("seller_pan_no", "")):
            return JSONResponse({"error": "seller PAN number must be 9 digits"}, status_code=400)
        if not _DATE.fullmatch(f.get("bill_date", "")):
            return JSONResponse({"error": "bill date must be YYYY-MM-DD"}, status_code=400)
        try:
            amount = float(f.get("billed_total_amount"))
        except ValueError:
            amount = 0.0
        if amount <= 0:
            return JSONResponse(
                {"error": "billed total amount must be greater than zero"}, status_code=400)
        if not 2 <= len(f.get("name", "").strip()) <= 120:
            return JSONResponse(
                {"error": "name must be between 2 and 120 characters"}, status_code=400)

        token = f.get("cf-turnstile-response", "")
        if not token or token == "FORCE_TURNSTILE_FAIL":
            return JSONResponse(TURNSTILE_FAILED, status_code=400)

        key = (f.get("seller_pan_no"), f.get("bill_number"), f.get("bill_date"))
        coupon = state["seen"].get(key)
        duplicate = coupon is not None
        if not duplicate:
            state["coupons"] += 1
            coupon = f"{state['coupons']:012d}"
            state["seen"][key] = coupon
        state["vids"] += 1
        return JSONResponse({
            "accepted": True,
            "duplicate": duplicate,
            "source": "MANUAL",
            "prize_coupon_number": coupon,
            "fiscal_year_code": "2083-84",
            "verification_id": f"VID-{state['vids']}",
            "saved_at": dt.datetime.now(dt.UTC).isoformat(),
        })

    return app


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


app = create_app()