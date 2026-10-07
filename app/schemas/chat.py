import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORM


class ChatMessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    document_id: uuid.UUID | None = None  # scope retrieval to one document


class Citation(ORM):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    title: str
    chunk_index: int
    snippet: str


class PendingAction(BaseModel):
    """An edit or delete the assistant wants to make, waiting for the user's decision."""

    interrupt_id: str
    action: Literal["update", "delete"]
    expense: dict  # the expense as it is now
    changes: dict | None = None  # for "update": only the fields that change
    decision: Literal["confirm", "cancel"] | None = None  # set once the user answered


class ChatMessageOut(ORM):
    id: uuid.UUID
    role: str
    content: str
    citations: list[Citation] | None = None
    pending_actions: list[PendingAction] | None = None
    created_at: dt.datetime


class ActionDecision(BaseModel):
    interrupt_id: str = Field(min_length=1, max_length=200)
    decision: Literal["confirm", "cancel"]


class ActionDecisionsIn(BaseModel):
    decisions: list[ActionDecision] = Field(min_length=1, max_length=20)


class ChatSessionOut(ORM):
    id: uuid.UUID
    title: str | None
    created_at: dt.datetime


class ChatSessionDetail(ChatSessionOut):
    messages: list[ChatMessageOut]