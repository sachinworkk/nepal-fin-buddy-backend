import contextlib
import io
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, status
from pypdf import PdfReader
from pypdf.errors import PyPdfError
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep, get_owned
from app.config import get_settings
from app.models import Document
from app.rag import index_document_safe
from app.schemas.documents import DocumentDetail, DocumentOut, NoteIn

router = APIRouter(prefix="/documents", tags=["documents"])
MAX_UPLOAD = 10 * 1024 * 1024
_ALLOWED = {"application/pdf": ("pdf", ".pdf"), "text/plain": ("note", ".txt")}


@router.post("", response_model=DocumentDetail, status_code=status.HTTP_201_CREATED)
async def create_note(body: NoteIn, user: CurrentUser, session: SessionDep):
    doc = Document(user_id=user.id, kind="note", title=body.title, text_content=body.text)
    session.add(doc)
    await session.commit()
    await session.refresh(doc)
    await index_document_safe(doc)
    return doc


@router.post("/upload", response_model=DocumentDetail, status_code=status.HTTP_201_CREATED)
async def upload_document(file: UploadFile, user: CurrentUser, session: SessionDep):
    if file.content_type not in _ALLOWED:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Only PDF or text files")
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large (10 MB max)")
    kind, ext = _ALLOWED[file.content_type]
    if kind == "pdf":
        try:
            text = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
        except (PyPdfError, ValueError):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unreadable PDF") from None
    else:
        text = data.decode("utf-8", errors="replace")
    doc_id = uuid.uuid4()
    path = Path(get_settings().storage_dir) / str(user.id) / f"{doc_id}{ext}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    doc = Document(
        id=doc_id, user_id=user.id, kind=kind, title=file.filename or f"{doc_id}{ext}",
        file_path=str(path), mime_type=file.content_type, text_content=text,
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)
    await index_document_safe(doc)
    return doc


@router.get("", response_model=list[DocumentOut])
async def list_documents(user: CurrentUser, session: SessionDep, kind: str | None = None):
    q = select(Document).where(Document.user_id == user.id)
    if kind:
        q = q.where(Document.kind == kind)
    return list(await session.scalars(q.order_by(Document.created_at.desc())))


@router.get("/{doc_id}", response_model=DocumentDetail)
async def get_document(doc_id: uuid.UUID, user: CurrentUser, session: SessionDep):
    return await get_owned(session, Document, doc_id, user)


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(doc_id: uuid.UUID, user: CurrentUser, session: SessionDep):
    doc = await get_owned(session, Document, doc_id, user)
    path = doc.file_path
    await session.delete(doc)
    await session.commit()
    if path:
        with contextlib.suppress(OSError):
            Path(path).unlink()
