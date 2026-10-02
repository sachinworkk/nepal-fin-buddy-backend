"""CBMS sync worker: polls cbms_sync_queue and submits due invoices.

Rows are claimed with SELECT ... FOR UPDATE SKIP LOCKED, so several workers can run
side by side. A claimed row is leased (status in_progress, next_attempt_at pushed out);
if the worker dies mid-submit the lease expires and the row is picked up again. That
resubmission is safe because CBMS answers 101 (already exists), which counts as synced.
"""

import asyncio
import datetime as dt
import random
import signal
import uuid

import httpx
import structlog
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cbms import messages
from app.cbms.client import make_http_client, submit
from app.cbms.messages import Outcome
from app.cbms.payload import build_payload
from app.config import get_settings
from app.core.crypto import decrypt
from app.models import CbmsSyncQueue, IrdCredential, IssuedInvoice

log = structlog.get_logger()

BATCH_SIZE = 10
LEASE = dt.timedelta(minutes=5)


def backoff_seconds(attempt: int, base: float, cap: float) -> float:
    """Exponential backoff with jitter: base * 2^(attempt-1), capped, scaled by 0.5-1.0."""
    return min(cap, base * 2 ** (attempt - 1)) * random.uniform(0.5, 1.0)


async def claim(
    maker: async_sessionmaker[AsyncSession], limit: int = BATCH_SIZE
) -> list[uuid.UUID]:
    async with maker() as session, session.begin():
        rows = (await session.scalars(
            select(CbmsSyncQueue)
            .where(CbmsSyncQueue.status.in_(("pending", "in_progress")),
                   CbmsSyncQueue.next_attempt_at <= func.now())
            .order_by(CbmsSyncQueue.next_attempt_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )).all()
        for q in rows:
            q.status = "in_progress"
            q.attempts += 1
            q.next_attempt_at = func.now() + LEASE
        return [q.id for q in rows]


async def _original_synced(session: AsyncSession, credit_note: IssuedInvoice) -> bool:
    return bool(await session.scalar(select(exists().where(
        IssuedInvoice.user_id == credit_note.user_id,
        IssuedInvoice.is_return.is_(False),
        IssuedInvoice.invoice_number == credit_note.ref_invoice_number,
        CbmsSyncQueue.issued_invoice_id == IssuedInvoice.id,
        CbmsSyncQueue.status == "synced",
    ))))


def _apply(q: CbmsSyncQueue, code: int | None, outcome: Outcome, delay: float) -> None:
    q.last_response_code = code
    if outcome.status == "synced":
        q.status, q.last_error, q.synced_at = "synced", None, func.now()
    elif outcome.status == "retry" and q.attempts < q.max_attempts:
        q.status, q.last_error = "pending", outcome.en
        q.next_attempt_at = func.now() + dt.timedelta(seconds=delay)
    else:
        q.status = "failed"
        q.last_error = messages.GAVE_UP.en if outcome.status == "retry" else outcome.en


async def process(
    maker: async_sessionmaker[AsyncSession], http: httpx.AsyncClient, queue_id: uuid.UUID
) -> None:
    s = get_settings()
    async with maker() as session:
        q = await session.get(CbmsSyncQueue, queue_id)
        if q is None:  # invoice deleted (e.g. account removed) since the claim
            return
        inv = await session.get(IssuedInvoice, q.issued_invoice_id)
        delay = backoff_seconds(q.attempts, s.cbms_backoff_base_seconds,
                                s.cbms_backoff_cap_seconds)
        cred = await session.scalar(
            select(IrdCredential).where(IrdCredential.user_id == inv.user_id)
        )
        if q.attempts > q.max_attempts:  # earlier runs crashed before recording a result
            code, outcome = None, messages.UNEXPECTED
        elif inv.is_return and not await _original_synced(session, inv):
            q.attempts -= 1  # waiting on the original doesn't use up an attempt
            code, outcome = None, messages.WAITING_FOR_ORIGINAL
        elif cred is None:
            code, outcome = None, messages.NO_CREDENTIALS
        else:
            payload = build_payload(
                inv, username=decrypt(cred.username_enc), password=decrypt(cred.password_enc),
                seller_pan=cred.seller_pan, realtime=q.attempts == 1,
            )
            code, outcome = await submit(http, q.endpoint, payload)
        _apply(q, code, outcome, delay)
        await session.commit()
        log.info("cbms.sync", queue_id=str(q.id), status=q.status, attempts=q.attempts, code=code)


async def run_once(maker: async_sessionmaker[AsyncSession], http: httpx.AsyncClient) -> int:
    """Claim and process one batch. Returns how many rows were claimed."""
    ids = await claim(maker)
    for queue_id in ids:
        try:
            await process(maker, http, queue_id)
        except Exception as e:
            # Left in_progress; the lease expiry retries it. Don't log the exception
            # itself: its traceback could include the payload.
            log.error("cbms.process_failed", queue_id=str(queue_id), error=type(e).__name__)
    return len(ids)


async def main() -> None:
    from app.db import SessionLocal

    poll = get_settings().worker_poll_seconds
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    log.info("cbms.worker_started", base_url=get_settings().cbms_base_url)
    async with make_http_client() as http:
        while not stop.is_set():
            try:
                claimed = await run_once(SessionLocal, http)
            except Exception as e:
                log.error("cbms.poll_failed", error=type(e).__name__)
                claimed = 0
            if claimed < BATCH_SIZE:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=poll)
                except TimeoutError:
                    pass


if __name__ == "__main__":
    asyncio.run(main())
