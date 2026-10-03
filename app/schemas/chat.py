import datetime as dt
import uuid

from pydantic import BaseModel, Field

from app.schemas.common import ORM


class ChatMessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class Citation(ORM):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    title: str
    chunk_index: int
    snippet: str


class ChatMessageOut(ORM):
    id: uuid.UUID
    role: str
    content: str
    citations: list[Citation] | None = None
    created_at: dt.datetime


class ChatSessionOut(ORM):
    id: uuid.UUID
    title: str | None
    created_at: dt.datetime


class ChatSessionDetail(ChatSessionOut):
    messages: list[ChatMessageOut]