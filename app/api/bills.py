import contextlib
import datetime as dt
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentUser, SessionDep, get_owned
from app.models import Bill, BillItem, Document, Expense, Extraction
from app.rag import index_document_safe
from app.schemas.bills import BillConfirm, BillOut, BillUpdate

router = APIRouter(prefix="/bills", tags=["bills"])


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
