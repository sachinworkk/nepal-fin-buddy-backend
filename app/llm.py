"""OpenRouter client: chat completions + embeddings, with LlmCallLog bookkeeping."""

import asyncio
import time

import httpx
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import LlmCallLog

log = structlog.get_logger()

_RETRYABLE = {408, 429, 500, 502, 503, 504}


class LlmError(Exception):
    def __init__(self, message: str, http_status: int | None = None):
        super().__init__(message)
        self.http_status = http_status


async def _post(session: AsyncSession, purpose: str, user_id, path: str, body: dict) -> dict:
    s = get_settings()
    key = s.openrouter_api_key
    if not key:
        raise LlmError("OPENROUTER_API_KEY is not configured", http_status=None)
    started = time.monotonic()
    attempt = 0
    while True:
        attempt += 1
        status_code = None
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{s.openrouter_base_url}{path}",
                    headers={"Authorization": f"Bearer {key}",
                             "Content-Type": "application/json"},
                    json=body,
                )
            status_code = resp.status_code
            if resp.status_code in _RETRYABLE and attempt <= 3:
                await _log(session, purpose, body.get("model"), None, None, None, None, None,
                           None, "error", resp.status_code, f"retryable http {resp.status_code}",
                           attempt)
                await asyncio.sleep(2 ** attempt)  # 2, 4, 8s
                continue
            if resp.status_code >= 400:
                raise LlmError(f"OpenRouter http {resp.status_code}", http_status=resp.status_code)
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            if attempt <= 3:
                await _log(session, purpose, body.get("model"), None, None, None, None, None,
                           None, "error", status_code, "http_error", attempt)
                await asyncio.sleep(2 ** attempt)
                continue
            raise LlmError("http_error", http_status=status_code) from None
        usage = data.get("usage") or {}
        latency = int((time.monotonic() - started) * 1000)
        gen_id = None
        gen = data.get("id")
        if isinstance(gen, str):
            gen_id = gen[:100]
        await _log(session, purpose, body.get("model"), gen_id,
                   usage.get("prompt_tokens"), usage.get("completion_tokens"), latency,
                   None, None, "ok", resp.status_code, None, attempt)
        return data


async def _log(session: AsyncSession, purpose: str, model: str | None, gen_id, prompt_toks,
               completion_toks, latency_ms, cost_usd, provider, status: str, http_status,
               error: str | None, attempt: int) -> None:
    if model is None:
        return
    session.add(LlmCallLog(
        purpose=purpose, model=model[:200], generation_id=gen_id,
        prompt_tokens=prompt_toks, completion_tokens=completion_toks,
        latency_ms=latency_ms, cost_usd=cost_usd, provider=provider[:100] if provider else None,
        status=status, http_status=http_status, error=error[:500] if error else None,
        attempt=attempt,
    ))
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        log.warning("llm.log_failed")


async def embed(session: AsyncSession, user_id, texts: list[str]) -> list[list[float]]:
    """Embed texts with the configured OpenRouter embedding model."""
    if not texts:
        return []
    s = get_settings()
    data = await _post(session, "embedding", user_id, "/embeddings", {
        "model": s.embed_model, "input": texts,
    })
    items = data.get("data") or []
    if len(items) != len(texts):
        raise LlmError("embedding count mismatch")
    return [item["embedding"] for item in items]


async def chat(session: AsyncSession, user_id, messages: list[dict]) -> str:
    """One-shot chat completion; returns the assistant message text."""
    s = get_settings()
    data = await _post(session, "chat", user_id, "/chat/completions", {
        "model": s.chat_model, "messages": messages,
    })
    choice = (data.get("choices") or [{}])[0]
    return (choice.get("message") or {}).get("content") or ""