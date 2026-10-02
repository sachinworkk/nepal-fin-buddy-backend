"""CBMS HTTP client. The payload carries IRD credentials, so it is never logged."""

import time

import httpx
import structlog

from app.cbms import messages
from app.cbms.messages import Outcome
from app.config import get_settings

log = structlog.get_logger()


def make_http_client() -> httpx.AsyncClient:
    # cbms_base_url refuses the live URL outside production.
    return httpx.AsyncClient(base_url=get_settings().cbms_base_url, timeout=20.0)


async def submit(
    http: httpx.AsyncClient, endpoint: str, payload: dict
) -> tuple[int | None, Outcome]:
    """POST to /api/{endpoint}. Returns (CBMS code or None, outcome)."""
    started = time.monotonic()
    try:
        r = await http.post(f"/api/{endpoint}", json=payload)
    except httpx.TransportError as e:
        log.warning("cbms.unreachable", endpoint=endpoint, error=type(e).__name__)
        return None, messages.UNREACHABLE
    latency_ms = int((time.monotonic() - started) * 1000)
    try:
        code = int(r.text.strip().strip('"')) if r.is_success else None
    except ValueError:
        code = None
    log.info("cbms.response", endpoint=endpoint, http_status=r.status_code, code=code,
             latency_ms=latency_ms)
    if code is None:
        return None, messages.UNEXPECTED
    return code, messages.for_code(code)
