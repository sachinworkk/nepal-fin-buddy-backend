"""HTTP client for the IRD prize portal (prize.ird.gov.np).

Public API, no credentials. The portal only speaks multipart form data and
plain ``{"error": "..."}`` strings. The Turnstile token and form_started_at
are per-submission values; they are never logged.
"""

import time

import httpx
import structlog

from app.config import get_settings

log = structlog.get_logger()

ENROLLMENTS = "/public/enrollments"
FISCAL_YEARS = "/public/fiscal-years"


class PrizeError(Exception):
    """An error returned by (or while reaching) the prize portal."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code  # None means the request never got a response


def make_prize_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=get_settings().prize_base_url, timeout=30.0)


async def get_prize_client() -> httpx.AsyncClient:
    """FastAPI dependency; tests override the transport via dependency_overrides."""
    async with make_prize_client() as http:
        yield http


def _parse_error(r: httpx.Response) -> str:
    try:
        return str(r.json().get("error") or f"prize portal returned HTTP {r.status_code}")
    except ValueError:
        return f"prize portal returned HTTP {r.status_code}"


async def fiscal_years(http: httpx.AsyncClient) -> dict:
    started = time.monotonic()
    try:
        r = await http.get(FISCAL_YEARS)
    except httpx.TransportError as e:
        log.warning("prize.unreachable", error=type(e).__name__)
        raise PrizeError("prize portal unreachable") from None
    log.info("prize.response", endpoint=FISCAL_YEARS, http_status=r.status_code,
             latency_ms=int((time.monotonic() - started) * 1000))
    if not r.is_success:
        raise PrizeError(_parse_error(r), r.status_code)
    try:
        return r.json()
    except ValueError:
        raise PrizeError(f"prize portal returned HTTP {r.status_code}", r.status_code) from None


async def submit_enrollment(
    http: httpx.AsyncClient, fields: dict[str, str], turnstile_token: str, form_started_at: int,
) -> dict:
    """POST /public/enrollments as multipart. Returns the portal's JSON response.

    Raises PrizeError with the portal's status code for validation/security
    errors, and status_code=None for transport failures (caller may retry).
    """
    data = {k: str(v) for k, v in fields.items()}
    data["cf-turnstile-response"] = turnstile_token
    data["form_started_at"] = str(form_started_at)
    started = time.monotonic()
    try:
        r = await http.post(ENROLLMENTS, data=data)
    except httpx.TransportError as e:
        log.warning("prize.unreachable", error=type(e).__name__)
        raise PrizeError("prize portal unreachable") from None
    log.info("prize.response", endpoint=ENROLLMENTS, http_status=r.status_code,
             latency_ms=int((time.monotonic() - started) * 1000))
    if not r.is_success:
        raise PrizeError(_parse_error(r), r.status_code)
    try:
        return r.json()
    except ValueError:
        raise PrizeError(f"prize portal returned HTTP {r.status_code}", r.status_code) from None