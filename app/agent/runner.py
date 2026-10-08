"""Builds and runs the Strands chat agent for one turn of one chat session.

Memory is per chat session: Strands snapshots the full conversation (including tool calls
and any pending confirmation) into agent_storage under the chat session's id, and restores
it on the next turn. Nothing is shared between chat sessions.
"""

import asyncio
import datetime as dt
import time
import uuid
from dataclasses import dataclass, field

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from strands import Agent
from strands.agent.conversation_manager import SlidingWindowConversationManager
from strands.hooks import AfterToolCallEvent
from strands.session import SnapshotSessionManager

from app.agent import model as agent_model
from app.agent.storage import PgStorage
from app.agent.tools import CONFIRM, TOOLS
from app.assistant import system_prompt
from app.config import get_settings
from app.llm import LlmError, _log
from app.models import DocumentChunk

log = structlog.get_logger()

WINDOW_SIZE = 40  # messages kept in the model's context, tool calls and results included


@dataclass
class TurnResult:
    text: str
    pending_actions: list[dict]
    audit: list[dict] = field(default_factory=list)


def _session_manager(chat_session_id: uuid.UUID) -> SnapshotSessionManager:
    return SnapshotSessionManager(session_id=str(chat_session_id), storage=PgStorage())


def _build_agent(chat_session_id: uuid.UUID, today: dt.date) -> Agent:
    agent = Agent(
        model=agent_model.build_model(), tools=TOOLS, callback_handler=None,
        session_manager=_session_manager(chat_session_id),
        conversation_manager=SlidingWindowConversationManager(
            window_size=WINDOW_SIZE, should_truncate_results=True),
    )
    # The restored snapshot carries the previous turn's prompt; today's date must be current.
    agent.system_prompt = system_prompt(today)
    agent.add_hook(_audit_tool_call, AfterToolCallEvent)
    return agent


def _audit_tool_call(event: AfterToolCallEvent) -> None:
    audit = event.invocation_state.get("audit")
    if audit is None:
        return
    result = event.result or {}
    text = " ".join(c.get("text", "") for c in result.get("content", []) if "text" in c)
    audit.append({"name": event.tool_use.get("name", ""),
                  "args": event.tool_use.get("input") or {}, "result": text[:2000]})


def _text(message: dict) -> str:
    return "".join(c["text"] for c in message.get("content", []) if "text" in c).strip()


async def run_turn(session: AsyncSession, user_id: uuid.UUID, chat_session_id: uuid.UUID,
                   prompt: str | list, chunks: list[DocumentChunk],
                   document_id: uuid.UUID | None = None,
                   after_cancel: bool = False) -> TurnResult:
    """Run one agent invocation. `prompt` is the user's text, or a list of interruptResponse
    blocks when resuming after the user confirmed or cancelled pending actions. `chunks` is
    extended with chunks found via search_documents so the caller can validate citations.
    `after_cancel` stops edits/deletes from asking for confirmation again within this turn, so
    cancelling is final instead of the model re-proposing the same change."""
    # Building the agent restores its snapshot through Strands' sync bridge; keep that off
    # the event loop.
    agent = await asyncio.to_thread(_build_agent, chat_session_id, dt.date.today())
    audit: list[dict] = []
    started = time.monotonic()
    model_id = get_settings().chat_model
    try:
        result = await agent.invoke_async(prompt, invocation_state={
            "user_id": user_id, "document_id": document_id, "chunks": chunks, "audit": audit,
            "no_confirm": after_cancel})
    except Exception as e:
        log.warning("agent.invoke_failed", error=str(e)[:300])
        await _log(session, "chat", model_id, None, None, None,
                   int((time.monotonic() - started) * 1000), None, None, "error", None,
                   type(e).__name__, 1)
        raise LlmError(f"agent failed: {type(e).__name__}") from e

    usage = result.metrics.accumulated_usage
    await _log(session, "chat", model_id, None, usage.get("inputTokens"),
               usage.get("outputTokens"), int((time.monotonic() - started) * 1000), None,
               None, "ok", 200, None, 1)
    pending = [{"interrupt_id": i.id, **(i.reason or {})}
               for i in (result.interrupts or []) if i.name == CONFIRM]
    return TurnResult(text=_text(result.message), pending_actions=pending, audit=audit)


async def delete_memory(chat_session_id: uuid.UUID) -> None:
    """Drop the agent's stored conversation for a deleted chat session."""
    await _session_manager(chat_session_id).delete_session()
