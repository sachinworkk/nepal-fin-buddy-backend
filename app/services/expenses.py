"""Expense writes shared by the REST routes and the chat agent's tools."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Bill, Expense
from app.schemas.common import resolve_date
from app.schemas.expenses import ExpenseBill, ExpenseIn, ExpenseUpdate


async def _set_bill_details(session, exp: Expense, details: ExpenseBill | None) -> None:
    """Create, update or (when empty) remove the image-less bill behind a manual expense."""
    if details is None or details.is_empty():
        if exp.bill is not None:
            await session.delete(exp.bill)  # prize enrollments keep their own copy
            exp.bill = None
        return
    if exp.bill is None:
        exp.bill = Bill(user_id=exp.user_id, status="confirmed")
    for k, v in details.model_dump().items():
        setattr(exp.bill, k, None if v == "" else v)


def _set_scanned_bill_details(exp: Expense, details: ExpenseBill | None) -> None:
    """Fill in or correct a scanned bill's tax-invoice details. The bill itself is never
    removed here, and its vendor, total and date stay as scanned."""
    for k, v in (details or ExpenseBill()).model_dump().items():
        setattr(exp.bill, k, None if v == "" else v)


def _sync_bill(exp: Expense) -> None:
    """Mirror the expense's vendor, total, date and payment method onto its manual bill."""
    if exp.bill is None or exp.source == "bill":
        return
    b = exp.bill
    b.vendor_name, b.total_amount, b.payment_method = exp.merchant, exp.amount, exp.payment_method
    b.bill_date_ad, b.bill_date_bs_raw = exp.expense_date_ad, exp.expense_date_bs_raw


async def create_expense(session: AsyncSession, user_id: uuid.UUID, body: ExpenseIn,
                         source: str = "manual") -> Expense:
    ad, bs = resolve_date(body.expense_date, body.expense_date_bs)
    exp = Expense(
        user_id=user_id, amount=body.amount, category=body.category.value,
        merchant=body.merchant, note=body.note, payment_method=body.payment_method,
        expense_date_ad=ad, expense_date_bs_raw=bs if body.expense_date_bs else None,
        source=source, bill=None,
    )
    await _set_bill_details(session, exp, body.bill)
    _sync_bill(exp)
    session.add(exp)
    await session.commit()
    await session.refresh(exp)
    return exp


async def update_expense(session: AsyncSession, exp: Expense, body: ExpenseUpdate) -> Expense:
    data = body.model_dump(exclude_unset=True, exclude={"bill"})
    if "bill" in body.model_fields_set:
        if exp.source == "bill" and exp.bill is not None:
            _set_scanned_bill_details(exp, body.bill)
        else:
            if exp.source == "bill" and body.bill is not None and not body.bill.is_empty():
                exp.source = "manual"  # its scanned bill was deleted; the new bill is manual
            await _set_bill_details(session, exp, body.bill)
    date_ad, date_bs = data.pop("expense_date", None), data.pop("expense_date_bs", None)
    if date_ad is not None or date_bs is not None:
        exp.expense_date_ad, bs = resolve_date(date_ad, date_bs)
        exp.expense_date_bs_raw = bs if date_bs else None
    if "category" in data and data["category"] is not None:
        data["category"] = data["category"].value
    for k, v in data.items():
        setattr(exp, k, v)
    _sync_bill(exp)
    await session.commit()
    await session.refresh(exp)
    return exp


async def delete_expense(session: AsyncSession, exp: Expense) -> None:
    if exp.bill is not None and exp.source != "bill":
        await session.delete(exp.bill)  # a manual expense's bill has no life of its own
    await session.delete(exp)
    await session.commit()
