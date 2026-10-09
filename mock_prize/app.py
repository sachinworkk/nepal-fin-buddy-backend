"""Mock of the IRD prize portal's public API (prize.ird.gov.np/api/v1), for tests.

Mirrors the behaviour observed on the real portal:
  - GET  /api/v1/public/fiscal-years
  - GET  /api/v1/public/winners?limit&offset&fiscal_year_code&category_id&from&to
  - POST /api/v1/public/enrollments (multipart, the cash-bill lottery entry)
Validation ladder order, error strings and the "form expired" time window match
the live service. Tokens must be non-empty; anything starting with "TEST_"
passes (tests). State (coupon counter, duplicates) is in memory and resets on
restart.
"""

import datetime as dt
import re
import time

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

_FISCAL_YEARS = {
    "fiscal_years": [
        {"fiscal_year_code": "2083-84", "display_name": "FY 2083/84",
         "start_date": "2026-07-17", "end_date": "2027-07-16", "status": "ACTIVE"},
        {"fiscal_year_code": "2082-83", "display_name": "FY 2082/83",
         "start_date": "2025-07-17", "end_date": "2026-07-16", "status": "CLOSED"},
    ],
}

# Coupon 000000000001 is the first one the mock issues, so a fresh test run can "win".
_WINNING_COUPON = "000000000001"
_DRAWS = [
    {
        "draw_id": "draw_mock_bumper", "_category_id": "category_bumper",
        "category_title_en": "Bumper Prize",
        "category_title_ne": "बम्पर पुरस्कार", "draw_type": "GENERAL",
        "title_en": "Bumper Winner Consumer Selection (mock)", "title_ne": "बम्पर विजेता (mock)",
        "eligible_from": "2026-09-17", "eligible_to": "2026-10-01",
        "published_at": "2026-10-02T13:54:27+05:45", "claim_deadline": "2026-10-17T13:54:27+05:45",
        "claim_open": True,
        "winners": [{"winner_rank": 1, "prize_fiscal_year_code": "2083-84",
                     "prize_coupon_number": _WINNING_COUPON}],
    },
    {
        "draw_id": "draw_mock_daily", "_category_id": "category_daily",
        "category_title_en": "Daily Prize",
        "category_title_ne": "दैनिक पुरस्कार", "draw_type": "GENERAL",
        "title_en": "Winner Consumer Selection (mock)", "title_ne": "विजेता उपभोक्ता (mock)",
        "eligible_from": "2026-09-17", "eligible_to": "2026-10-01",
        "published_at": "2026-10-02T13:52:46+05:45", "claim_deadline": "2026-10-17T13:52:46+05:45",
        "claim_open": True,
        "winners": [{"winner_rank": i, "prize_fiscal_year_code": "2083-84",
                     "prize_coupon_number": f"{900000000000 + i}"} for i in range(1, 4)],
    },
]

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

    @app.get("/api/v1/public/winners")
    async def winners(
        limit: int = 20, offset: int = 0, fiscal_year_code: str | None = None,
        category_id: str | None = None, from_: str | None = Query(None, alias="from"),
        to: str | None = None,
    ):
        for name, v in (("from", from_), ("to", to)):
            if v and not _DATE.fullmatch(v):
                return JSONResponse({"error": f"{name} must be YYYY-MM-DD"}, status_code=400)
        limit = min(max(limit, 1), 25)  # the real portal silently caps at 25
        draws = [] if fiscal_year_code not in (None, "2083-84") else list(_DRAWS)
        if category_id:
            draws = [d for d in draws if d["_category_id"] == category_id]
        # Like the portal: a draw matches when its whole eligibility window lies in [from, to].
        if from_:
            draws = [d for d in draws if d["eligible_from"] >= from_]
        if to:
            draws = [d for d in draws if d["eligible_to"] <= to]
        page = [{k: v for k, v in d.items() if not k.startswith("_")}
                for d in draws[offset:offset + limit]]
        return {
            "limit": limit, "offset": offset, "total_draws": len(draws),
            "has_more": offset + limit < len(draws),
            "fiscal_years": [
                {"fiscal_year_code": "2083-84", "display_name": "FY 2083/84", "winner_count": 4},
                {"fiscal_year_code": "2082-83", "display_name": "FY 2082/83", "winner_count": 0},
            ],
            "categories": [
                {"category_id": "category_bumper", "title_en": "Bumper Prize",
                 "title_ne": "बम्पर पुरस्कार"},
                {"category_id": "category_daily", "title_en": "Daily Prize",
                 "title_ne": "दैनिक पुरस्कार"},
            ],
            "draws": page,
        }

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