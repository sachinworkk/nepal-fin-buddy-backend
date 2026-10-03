import json
import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep
from app.llm import LlmError, chat
from app.models import ChatMessage, ChatSession
from app.rag import retrieve
from app.schemas.chat import (
    ChatMessageIn,
    ChatMessageOut,
    ChatSessionDetail,
    ChatSessionOut,
    Citation,
)

router = APIRouter(prefix="/chat", tags=["chat"])

SYSTEM_PROMPT = """You are Nepal Fin Buddy's assistant. You help the user understand their \
finances using their uploaded bills, documents and expenses.
Rules:
- Ground every factual claim in the provided context; if the context doesn't contain the \
answer, say you don't have that information instead of guessing.
- Amounts are in Nepali Rupees (Rs.). Format them like Rs. 1,234.56.
- Dates may be in Bikram Sambat (BS) as well as Gregorian (AD); keep the original format \
when quoting them.
- VAT amounts on bills are 13% of the taxable amount in Nepal; do not "correct" the data.
- Be concise."""


async def _owned_session(session_dep, user, session_id: uuid.UUID) -> ChatSession:
    cs = await session_dep.get(ChatSession, session_id)
    if cs is None or cs.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return cs


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
        select(ChatMessage).where(ChatMessage.session_id == cs.id)
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


@router.post("/sessions/{session_id}/messages", response_model=ChatMessageOut)
async def send_message(session_id: uuid.UUID, body: ChatMessageIn,
                       user: CurrentUser, session: SessionDep):
    cs = await _owned_session(session, user, session_id)
    history = list(await session.scalars(
        select(ChatMessage).where(ChatMessage.session_id == cs.id)
        .order_by(ChatMessage.created_at.desc()).limit(10)
    ))
    history.reverse()

    try:
        chunks = await retrieve(session, user.id, body.content, top_k=6)
    except LlmError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Embedding failed: {e}") from None

    user_msg = ChatMessage(session_id=cs.id, role="user", content=body.content)
    session.add(user_msg)
    await session.commit()

    context = "\n\n---\n\n".join(
        f"[{c.meta.get('title', 'document')} | chunk {c.chunk_index}]\n{c.content}"
        for c in chunks
    )
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += [{"role": m.role, "content": m.content} for m in history]
    if context:
        messages.append({
            "role": "system",
            "content": f"Context from the user's documents:\n\n{context}",
        })
    messages.append({"role": "user", "content": body.content})

    try:
        answer = await chat(session, user.id, messages)
    except LlmError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"LLM call failed: {e}") from None
    if not answer:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "LLM returned an empty response")

    citations = [Citation(
        chunk_id=c.id, document_id=c.document_id,
        title=c.meta.get("title", "document"), chunk_index=c.chunk_index,
        snippet=c.content[:200],
    ) for c in chunks]

    ai_msg = ChatMessage(session_id=cs.id, role="assistant", content=answer,
                         citations=[json.loads(c.model_dump_json()) for c in citations])
    session.add(ai_msg)
    await session.commit()
    await session.refresh(ai_msg)

    out = ChatMessageOut.model_validate(ai_msg)
    out.citations = citations
    return out