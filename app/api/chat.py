import json
import re
import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.agent.runner import TurnResult, delete_memory, run_turn
from app.api.deps import CurrentUser, SessionDep
from app.llm import LlmError
from app.models import ChatMessage, ChatSession, Document
from app.observability import trace_span
from app.rag import retrieve
from app.schemas.chat import (
    ActionDecisionsIn,
    ChatMessageIn,
    ChatMessageOut,
    ChatSessionDetail,
    ChatSessionOut,
    Citation,
)

router = APIRouter(prefix="/chat", tags=["chat"])

PENDING_TEXT = "Please confirm the change below."


async def _owned_session(session_dep, user, session_id: uuid.UUID) -> ChatSession:
    cs = await session_dep.get(ChatSession, session_id)
    if cs is None or cs.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return cs


async def _pending_message(session, cs_id: uuid.UUID) -> ChatMessage | None:
    """The latest assistant message, if it still waits on the user to confirm/cancel."""
    last = await session.scalar(
        select(ChatMessage)
        .where(ChatMessage.session_id == cs_id, ChatMessage.role == "assistant")
        .order_by(ChatMessage.created_at.desc()).limit(1))
    if last and any(a.get("decision") is None for a in last.pending_actions or []):
        return last
    return None


def _citation(c) -> Citation:
    return Citation(chunk_id=c.id, document_id=c.document_id,
                    title=c.meta.get("title", "document"), chunk_index=c.chunk_index,
                    snippet=c.content[:200])


def _citations(answer: str, chunks: list, tool_audit: list[dict]) -> list[Citation]:
    by_ref = {(c.meta.get("title", "document"), c.chunk_index): c for c in chunks}
    cited = set(re.findall(r"\[([^|\]]+)\s*\|\s*chunk\s*(\d+)\]", answer))
    valid = [by_ref[(t.strip(), int(n))] for t, n in cited if (t.strip(), int(n)) in by_ref]
    if valid:
        # Keep only citations the model actually used, in retrieval order.
        keep = set(id(c) for c in valid)
        return [_citation(c) for c in chunks if id(c) in keep]
    if not chunks or any(t["name"] != "search_documents" for t in tool_audit):
        return []  # answered from expense tools; retrieved context wasn't the source
    # Model ignored the citation format; surface the retrieved context as before.
    return [_citation(c) for c in chunks]


async def _save_turn(session, cs_id: uuid.UUID, turn: TurnResult, chunks: list,
                     span) -> ChatMessageOut:
    """Persist the turn's tool rows and assistant message, and shape the response."""
    if turn.pending_actions:
        answer, citations = turn.text or PENDING_TEXT, []
    else:
        if not turn.text:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "LLM returned an empty response")
        answer, citations = turn.text, _citations(turn.text, chunks, turn.audit)
    span.set(chunks=len(chunks), tool_calls=len(turn.audit), answer_chars=len(answer),
             citations=len(citations), pending_actions=len(turn.pending_actions))
    span.io(outputs={"answer": answer})
    for t in turn.audit:
        session.add(ChatMessage(session_id=cs_id, role="tool", content=t["result"],
                                tool_name=t["name"][:50], tool_args=t["args"]))
    ai_msg = ChatMessage(session_id=cs_id, role="assistant", content=answer,
                         citations=[json.loads(c.model_dump_json()) for c in citations],
                         pending_actions=turn.pending_actions or None)
    session.add(ai_msg)
    await session.commit()
    await session.refresh(ai_msg)
    out = ChatMessageOut.model_validate(ai_msg)
    out.citations = citations
    return out


@router.post("/sessions", response_model=ChatSessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(user: CurrentUser, session: SessionDep):
    cs = ChatSession(user_id=user.id, kind="assistant")
    session.add(cs)
    await session.commit()
    await session.refresh(cs)
    return cs


@router.get("/sessions", response_model=list[ChatSessionOut])
async def list_sessions(user: CurrentUser, session: SessionDep):
    rows = await session.scalars(
        select(ChatSession).where(ChatSession.user_id == user.id)
        .order_by(ChatSession.created_at.desc())
    )
    return list(rows)


@router.get("/sessions/{session_id}", response_model=ChatSessionDetail)
async def get_session(session_id: uuid.UUID, user: CurrentUser, session: SessionDep):
    cs = await _owned_session(session, user, session_id)
    msgs = list(await session.scalars(
        select(ChatMessage)
        .where(ChatMessage.session_id == cs.id, ChatMessage.role != "tool")
        .order_by(ChatMessage.created_at)
    ))
    out = ChatSessionOut.model_validate(cs).model_dump()
    out["messages"] = [ChatMessageOut.model_validate(m).model_dump(mode="json") for m in msgs]
    return ChatSessionDetail.model_validate(out)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: uuid.UUID, user: CurrentUser, session: SessionDep):
    cs = await _owned_session(session, user, session_id)
    await session.delete(cs)
    await session.commit()
    await delete_memory(cs.id)


@router.post("/sessions/{session_id}/messages", response_model=ChatMessageOut)
async def send_message(session_id: uuid.UUID, body: ChatMessageIn,
                       user: CurrentUser, session: SessionDep):
    # No user id on the trace; query/answer text only when MLFLOW_LOG_CONTENT is on.
    with trace_span("chat.turn", span_type="AGENT", scoped=body.document_id is not None) as span:
        return await _send_message(session_id, body, user, session, span)


async def _send_message(session_id: uuid.UUID, body: ChatMessageIn, user, session, span):
    span.io(inputs={"query": body.content})
    cs = await _owned_session(session, user, session_id)
    user_id, cs_id = user.id, cs.id
    if await _pending_message(session, cs_id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Confirm or cancel the pending action first")

    if body.document_id is not None:
        doc = await session.get(Document, body.document_id)
        if doc is None or doc.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")

    # Retrieval query: the current message plus the last user turn, so follow-up questions
    # like "and how much was the VAT?" still resolve their referent. (The agent itself
    # remembers the whole session.)
    last_user = await session.scalar(
        select(ChatMessage.content)
        .where(ChatMessage.session_id == cs_id, ChatMessage.role == "user")
        .order_by(ChatMessage.created_at.desc()).limit(1))
    retrieval_query = body.content
    if last_user and last_user != body.content:
        retrieval_query = f"{last_user}\n{body.content}"

    try:
        chunks = await retrieve(session, user_id, retrieval_query, top_k=6,
                                document_id=body.document_id)
    except LlmError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Embedding failed: {e}") from None

    session.add(ChatMessage(session_id=cs_id, role="user", content=body.content))
    await session.commit()

    prompt = body.content
    if chunks:
        context = "\n\n---\n\n".join(
            f"[{c.meta.get('title', 'document')} | chunk {c.chunk_index}]\n{c.content}"
            for c in chunks)
        prompt = (f"Context from the user's documents:\n\n{context}\n\n---\n\n"
                  f"User message: {body.content}")

    try:
        turn = await run_turn(session, user_id, cs_id, prompt, chunks,
                              document_id=body.document_id)
    except LlmError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"LLM call failed: {e}") from None
    return await _save_turn(session, cs_id, turn, chunks, span)


@router.post("/sessions/{session_id}/actions", response_model=ChatMessageOut)
async def resolve_actions(session_id: uuid.UUID, body: ActionDecisionsIn,
                          user: CurrentUser, session: SessionDep):
    """Confirm or cancel the edits/deletes the assistant paused on, then let it finish."""
    with trace_span("chat.actions", span_type="AGENT") as span:
        cs = await _owned_session(session, user, session_id)
        user_id, cs_id = user.id, cs.id
        pending = await _pending_message(session, cs_id)
        if pending is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "No action is waiting for a decision")
        decisions = {d.interrupt_id: d.decision for d in body.decisions}
        if set(decisions) != {a["interrupt_id"] for a in pending.pending_actions}:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "Decide every pending action, and only those")

        responses = [{"interruptResponse": {"interruptId": i, "response": d}}
                     for i, d in decisions.items()]
        try:
            turn = await run_turn(session, user_id, cs_id, responses, [])
        except LlmError as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"LLM call failed: {e}") from None
        pending.pending_actions = [{**a, "decision": decisions[a["interrupt_id"]]}
                                   for a in pending.pending_actions]
        return await _save_turn(session, cs_id, turn, [], span)
