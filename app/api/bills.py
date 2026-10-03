import contextlib
import datetime as dt
import hashlib
import json
import uuid
from pathlib import Path

import structlog
from fastapi import APIRouter, HTTPException, UploadFile, status
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep, get_owned
from app.config import get_settings
from app.core.dates import parse_date_any
from app.extraction import extract_bill
from app.llm import LlmError
from app.models import Bill, BillItem, Document, Expense, Extraction
from app.rag import index_document_safe
from app.schemas.bills import BillConfirm, BillOut, BillUpdate

log = structlog.get_logger()

router = APIRouter(prefix="/bills", tags=["bills"])
MAX_BILL_UPLOAD = 10 * 1024 * 1024  # 10 MB
_ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
        "application/pdf": ".pdf"}


@router.post("/upload", response_model=BillOut, status_code=status.HTTP_201_CREATED)
async def upload_bill(file: UploadFile, user: CurrentUser, session: SessionDep):
    """Accept a bill photo/PDF, run LLM extraction, persist the Bill + Extraction.

    Resubmission of the same file is idempotent: an identical sha256 for the user
    returns the existing bill instead of extracting again.
    """
    if file.content_type not in _ALLOWED_MIME:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "Only JPEG, PNG, WebP images or PDF bills",
        )
    data = await file.read(MAX_BILL_UPLOAD + 1)
    if len(data) > MAX_BILL_UPLOAD:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large (10 MB max)")

    sha = hashlib.sha256(data).hexdigest()
    existing = await session.scalar(
        select(Bill).where(Bill.user_id == user.id, Bill.image_sha256 == sha)
    )
    if existing:
        return existing

    try:
        fields = await extract_bill(session, user.id, data, file.content_type)
    except LlmError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Extraction failed: {e}") from None

    # Normalise the printed date (BS or AD) to the AD date + raw BS string.
    date_src = fields.get("bill_date_bs") or fields.get("bill_date_ad")
    if date_src:
        try:
            bill_date_ad, bs_raw = parse_date_any(date_src)
            fields["bill_date_ad"] = bill_date_ad
            fields["bill_date_bs_raw"] = bs_raw
        except ValueError:
            log.warning("bill.date_unparseable", raw=date_src)

    doc_id = uuid.uuid4()
    ext = _EXT[file.content_type]
    path = Path(get_settings().storage_dir) / str(user.id) / "bills" / f"{doc_id}{ext}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)

    items = fields.pop("items") or []
    bill = Bill(
        user_id=user.id, image_path=str(path), image_sha256=sha,
        mime_type=file.content_type, status="extracted",
        extraction_confidence=None,
        **{k: v for k, v in fields.items() if k in Bill.__table__.columns.keys()},
    )
    bill.items = [BillItem(description=i["description"], quantity=i.get("quantity"),
                           unit_price=i.get("unit_price"), amount=i.get("amount"))
                  for i in items]
    session.add(bill)
    await session.commit()
    await session.refresh(bill)

    session.add(Extraction(
        bill_id=bill.id, extractor="llm_vision", model=get_settings().extraction_model,
        prompt_version="v1",
        structured=json.loads(json.dumps(fields, default=str)),
        raw_output=json.dumps(fields, default=str),
        ocr_text=fields.get("ocr_text"),
    ))
    await session.commit()
    return bill


def _apply(bill: Bill, body: BillUpdate) -> None:
    data = body.model_dump(exclude_unset=True, exclude={"category"})
    items = data.pop("items", None)
    for k, v in data.items():
        setattr(bill, k, v)
    if items is not None:
        bill.items = [BillItem(**i) for i in items]


@router.get("", response_model=list[BillOut])
async def list_bills(
    user: CurrentUser, session: SessionDep, status_: str | None = None,
    date_from: dt.date | None = None, date_to: dt.date | None = None,
):
    q = select(Bill).where(Bill.user_id == user.id)
    if status_:
        q = q.where(Bill.status == status_)
    if date_from:
        q = q.where(Bill.bill_date_ad >= date_from)
    if date_to:
        q = q.where(Bill.bill_date_ad <= date_to)
    return list(await session.scalars(q.order_by(Bill.created_at.desc())))


@router.get("/{bill_id}", response_model=BillOut)
async def get_bill(bill_id: uuid.UUID, user: CurrentUser, session: SessionDep):
    return await get_owned(session, Bill, bill_id, user)


@router.patch("/{bill_id}", response_model=BillOut)
async def update_bill(bill_id: uuid.UUID, body: BillUpdate, user: CurrentUser, session: SessionDep):
    bill = await get_owned(session, Bill, bill_id, user)
    if bill.status != "extracted":
        raise HTTPException(status.HTTP_409_CONFLICT, "Bill is already confirmed")
    _apply(bill, body)
    await session.commit()
    return bill


@router.post("/{bill_id}/confirm", response_model=BillOut)
async def confirm_bill(
    bill_id: uuid.UUID, body: BillConfirm, user: CurrentUser, session: SessionDep
):
    bill = await get_owned(session, Bill, bill_id, user)
    if bill.status != "extracted":
        raise HTTPException(status.HTTP_409_CONFLICT, "Bill is already confirmed")
    _apply(bill, body)
    bill.status = "confirmed"
    session.add(Expense(
        user_id=user.id, amount=body.total_amount, category=body.category.value,
        merchant=bill.vendor_name, expense_date_ad=body.bill_date_ad,
        expense_date_bs_raw=bill.bill_date_bs_raw, source="bill", bill_id=bill.id,
    ))
    await session.commit()
    await session.refresh(bill)
    await _index_bill(session, bill)
    return bill


async def _index_bill(session, bill: Bill) -> None:
    """Create/refresh the bill's searchable document and index it for RAG."""
    extraction_text = await session.scalar(
        select(Extraction.ocr_text).where(Extraction.bill_id == bill.id)
        .order_by(Extraction.created_at.desc()).limit(1)
    )
    if not extraction_text:
        return
    doc = await session.scalar(
        select(Document).where(Document.bill_id == bill.id, Document.kind == "bill")
    )
    if doc is None:
        doc = Document(
            user_id=bill.user_id, kind="bill", bill_id=bill.id,
            title=bill.vendor_name or f"Bill {bill.bill_number or bill.id}",
            mime_type=bill.mime_type,
        )
    doc.text_content = extraction_text
    session.add(doc)
    await session.commit()
    await session.refresh(doc)
    await index_document_safe(doc)


@router.delete("/{bill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bill(bill_id: uuid.UUID, user: CurrentUser, session: SessionDep):
    bill = await get_owned(session, Bill, bill_id, user)
    path = bill.image_path
    await session.delete(bill)
    await session.commit()
    if path:
        with contextlib.suppress(OSError):
            Path(path).unlink()
