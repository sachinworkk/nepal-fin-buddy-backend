import datetime as dt
import uuid

from pydantic import BaseModel, Field

from app.schemas.common import ORM


class NoteIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1)


class DocumentOut(ORM):
    id: uuid.UUID
    kind: str
    title: str
    mime_type: str | None
    bill_id: uuid.UUID | None
    created_at: dt.datetime


class DocumentDetail(DocumentOut):
    text_content: str
