"""Cloudflare Turnstile server-side verification (siteverify).

The frontend solves the widget; the token is only trustworthy once it has been
checked against the widget's secret key. Test secrets (1x/2x/3x) never touch
the network: 1x passes unconditionally, 2x/3x fail unconditionally.
"""

import httpx
import structlog

from app.config import get_settings

log = structlog.get_logger()

SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

TEST_SECRET_PREFIXES = ("1x", "2x", "3x")


class TurnstileError(Exception):
    """The token failed siteverify (or siteverify could not be reached)."""


async def verify_turnstile(token: str, remoteip: str | None = None) -> None:
    """Raise TurnstileError unless `token` verifies against the configured secret."""
    secret = get_settings().prize_turnstile_secret_key
    if secret.startswith(TEST_SECRET_PREFIXES):
        if secret.startswith("1x"):
            return
        raise TurnstileError(
            "security verification failed or expired; please complete it again"
        )
    data = {"secret": secret, "response": token}
    if remoteip:
        data["remoteip"] = remoteip
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.post(SITEVERIFY_URL, data=data)
        outcome = r.json()
    except (httpx.TransportError, ValueError):
        log.warning("turnstile.siteverify_unreachable")
        raise TurnstileError(
            "security verification is unavailable right now — please try again shortly"
        ) from None
    if not outcome.get("success"):
        log.info("turnstile.verify_failed", codes=outcome.get("error-codes") or [])
        raise TurnstileError(
            "security verification failed or expired; please complete it again"
        )
