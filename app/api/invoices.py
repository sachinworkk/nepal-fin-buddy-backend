import datetime as dt
import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, SessionDep, get_owned
from app.core import dates
from app.models import CbmsSyncQueue, IssuedInvoice
from app.schemas.invoices import InvoiceIn, InvoiceOut, SyncStatusOut

router = APIRouter(prefix="/invoices", tags=["invoices"])


async def _out(session, inv: IssuedInvoice) -> InvoiceOut:
    out = InvoiceOut.model_validate(inv)
    q = await session.scalar(
        select(CbmsSyncQueue).where(CbmsSyncQueue.issued_invoice_id == inv.id)
    )
    out.sync = SyncStatusOut.model_validate(q) if q else None
    return out


@router.post("", response_model=InvoiceOut, status_code=status.HTTP_201_CREATED)
async def create_invoice(body: InvoiceIn, user: CurrentUser, session: SessionDep):
    y, m, d = _parse_bs(body.invoice_date_bs)
    ad = dates.bs_to_ad(y, m, d)
    data = body.model_dump()
    data["invoice_date_bs"] = dates.format_bs(y, m, d)
    if body.is_return:
        cy, cm, cd = _parse_bs(body.credit_note_date_bs)
        data["credit_note_date_bs"] = dates.format_bs(cy, cm, cd)
        ref = await session.scalar(select(IssuedInvoice.id).where(
            IssuedInvoice.user_id == user.id, IssuedInvoice.is_return.is_(False),
            IssuedInvoice.invoice_number == body.ref_invoice_number,
        ))
        if ref is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "Referenced invoice not found")
    inv = IssuedInvoice(
        user_id=user.id, invoice_date_ad=ad,
        fiscal_year=dates.fiscal_year_for(ad), **data,
    )
    session.add(inv)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Invoice number already used") from None
    session.add(CbmsSyncQueue(
        issued_invoice_id=inv.id, endpoint="billreturn" if body.is_return else "bill",
    ))
    await session.commit()
    await session.refresh(inv)
    return await _out(session, inv)


def _parse_bs(text: str | None) -> tuple[int, int, int]:
    try:
        return dates.parse_bs_string(text or "")
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from None


@router.get("", response_model=list[InvoiceOut])
async def list_invoices(user: CurrentUser, session: SessionDep, fiscal_year: str | None = None):
    q = select(IssuedInvoice).where(IssuedInvoice.user_id == user.id)
    if fiscal_year:
        q = q.where(IssuedInvoice.fiscal_year == fiscal_year)
    rows = await session.scalars(q.order_by(IssuedInvoice.created_at.desc()))
    return [await _out(session, i) for i in rows]


@router.get("/{invoice_id}", response_model=InvoiceOut)
async def get_invoice(invoice_id: uuid.UUID, user: CurrentUser, session: SessionDep):
    return await _out(session, await get_owned(session, IssuedInvoice, invoice_id, user))


@router.post("/{invoice_id}/resync", response_model=InvoiceOut)
async def resync_invoice(invoice_id: uuid.UUID, user: CurrentUser, session: SessionDep):
    inv = await get_owned(session, IssuedInvoice, invoice_id, user)
    q = await session.scalar(
        select(CbmsSyncQueue).where(CbmsSyncQueue.issued_invoice_id == inv.id).with_for_update()
    )
    if q is None or q.status != "failed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only failed submissions can be re-synced")
    q.status, q.attempts, q.last_error = "pending", 0, None
    q.next_attempt_at = dt.datetime.now(dt.UTC)
    await session.commit()
    return await _out(session, inv)
