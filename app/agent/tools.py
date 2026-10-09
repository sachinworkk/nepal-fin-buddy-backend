"""Strands tools for the chat agent.

user_id, document_id and the shared retrieved-chunks list come from the invocation state the
API passes in, never from model arguments, so the model can't widen a query past the caller's
own rows. Each tool opens its own DB session: Strands runs async tools concurrently.
Edits and deletes pause on an interrupt until the user confirms them in the UI.
"""

import datetime as dt
import json
import uuid
from decimal import Decimal
from typing import Literal

from pydantic import ValidationError
from sqlalchemy import select
from strands import ToolContext, tool

from app import assistant
from app.assistant import AggregateArgs, ListArgs, SearchArgs
from app.core.categories import Category
from app.core.payment_methods import PaymentMethod
from app.db import SessionLocal
from app.models import Expense
from app.observability import trace_span
from app.rag import retrieve
from app.schemas.budgets import BudgetIn
from app.schemas.expenses import ExpenseIn, ExpenseUpdate
from app.services import budgets, expenses
from app.services.prize import eligible_bills, pending_handoffs

CONFIRM = "expense-confirm"


def _error(msg: str) -> dict:
    return {"status": "error", "content": [{"text": msg}]}


def _user(ctx: ToolContext) -> uuid.UUID:
    return ctx.invocation_state["user_id"]


def _summary(e: Expense) -> dict:
    return {"id": str(e.id), "date": e.expense_date_ad.isoformat(),
            "date_bs": e.expense_date_bs_raw, "amount": str(e.amount),
            "category": e.category, "merchant": e.merchant, "note": e.note,
            "payment_method": e.payment_method}


def _cancelled(ctx: ToolContext) -> dict | None:
    """Set while resuming after a cancel: the user just declined, so don't ask again."""
    if ctx.invocation_state.get("no_confirm"):
        return _error("The user just cancelled. Don't propose this change again; "
                      "acknowledge it and ask what they'd like instead.")
    return None


def _validation_text(e: ValidationError) -> str:
    return "Invalid arguments: " + "; ".join(
        f"{'.'.join(map(str, x['loc'])) or 'input'}: {x['msg']}" for x in e.errors())


async def _owned_expense(session, user_id: uuid.UUID, expense_id: str) -> Expense | None:
    try:
        eid = uuid.UUID(expense_id)
    except ValueError:
        return None
    return await session.scalar(
        select(Expense).where(Expense.id == eid, Expense.user_id == user_id))


@tool(context=True)
async def aggregate_expenses(
    tool_context: ToolContext,
    metric: Literal["sum", "count", "avg"] = "sum",
    group_by: Literal["none", "category", "merchant", "month"] = "none",
    date_from: str | None = None, date_to: str | None = None,
    category: Category | None = None, merchant: str | None = None, top_n: int = 10,
) -> dict | str:
    """Sum, count or average the user's expenses, optionally grouped and filtered.

    Args:
        metric: What to compute.
        group_by: How to group the rows.
        date_from: Inclusive AD start date (YYYY-MM-DD).
        date_to: Inclusive AD end date (YYYY-MM-DD).
        category: Only this expense category.
        merchant: Only merchants whose name contains this text.
        top_n: Max groups to return (1-20).
    """
    args = {k: v for k, v in locals().items() if k != "tool_context" and v is not None}
    try:
        a = AggregateArgs(**args)
    except ValidationError as e:
        return _error(_validation_text(e))
    with trace_span("tool.aggregate_expenses", span_type="TOOL"):
        async with SessionLocal() as s:
            return json.dumps(await assistant.aggregate_expenses(s, _user(tool_context), a))


@tool(context=True)
async def list_expenses(
    tool_context: ToolContext,
    date_from: str | None = None, date_to: str | None = None,
    category: Category | None = None, merchant: str | None = None, limit: int = 20,
) -> dict | str:
    """List individual expenses with their ids, newest first (max 50). Use the ids to edit or
    delete an expense.

    Args:
        date_from: Inclusive AD start date (YYYY-MM-DD).
        date_to: Inclusive AD end date (YYYY-MM-DD).
        category: Only this expense category.
        merchant: Only merchants whose name contains this text.
        limit: Max expenses to return (1-50).
    """
    args = {k: v for k, v in locals().items() if k != "tool_context" and v is not None}
    try:
        a = ListArgs(**args)
    except ValidationError as e:
        return _error(_validation_text(e))
    with trace_span("tool.list_expenses", span_type="TOOL"):
        async with SessionLocal() as s:
            return json.dumps(await assistant.list_expenses(s, _user(tool_context), a))


@tool(context=True)
async def search_documents(tool_context: ToolContext, query: str,
                           document_id: str | None = None) -> dict | str:
    """Search the user's bills and documents; returns text chunks with titles.

    Args:
        query: What to look for.
        document_id: Optional id of one document to search in.
    """
    try:
        sa = SearchArgs(query=query, document_id=document_id)
    except ValidationError as e:
        return _error(_validation_text(e))
    state = tool_context.invocation_state
    chunks = state["chunks"]
    with trace_span("tool.search_documents", span_type="TOOL"):
        async with SessionLocal() as s:
            # A scoped chat stays scoped; the model can't widen it.
            found = await retrieve(s, _user(tool_context), sa.query, top_k=6,
                                   document_id=state.get("document_id") or sa.document_id)
    known = {c.id for c in chunks}
    chunks.extend(c for c in found if c.id not in known)
    return assistant.chunk_text(found) or "No matching documents."


@tool(context=True)
async def list_prize_eligible_bills(tool_context: ToolContext) -> dict | str:
    """List the user's bills that can be entered in the IRD bill lottery (Bill Magnu Bhagya
    Pau): confirmed, cash, with PAN, bill number, date and total, and not already submitted.
    Bills missing any of those details are not eligible; bills are submitted from the app's
    Prize screen, not by you. Each bill's status is "ready_to_submit" (not started yet) or
    "awaiting_portal_submission" (prepared for the IRD portal, waiting for the user to finish
    submitting there and mark it submitted)."""
    with trace_span("tool.list_prize_eligible_bills", span_type="TOOL"):
        async with SessionLocal() as s:
            bills = await eligible_bills(s, _user(tool_context))
            pending = await pending_handoffs(s, _user(tool_context))
    return json.dumps({"currency": "NPR", "eligible_bills": [
        {"vendor": vendor, "pan": e.seller_pan_no, "bill_number": e.bill_number,
         "date": e.bill_date.isoformat(), "total": str(e.billed_total_amount),
         "status": "awaiting_portal_submission"} for e, vendor in pending] + [
        {"bill_id": str(b.id), "vendor": b.vendor_name, "pan": b.vendor_pan,
         "bill_number": b.bill_number, "date": b.bill_date_ad.isoformat(),
         "total": str(b.total_amount), "status": "ready_to_submit"} for b in bills]})


@tool(context=True)
async def get_budgets(tool_context: ToolContext) -> dict | str:
    """List the user's monthly category budgets with how much of each has been spent so far
    in the current Bikram Sambat month. Use it for any question about budgets, limits or
    whether the user is over or close to budget. Each entry has the budget amount, spent,
    remaining (negative when over), percent used and a status of "ok", "warning" (80% or more
    used) or "over"."""
    with trace_span("tool.get_budgets", span_type="TOOL"):
        async with SessionLocal() as s:
            rows = await budgets.budget_status(s, _user(tool_context))
    if not rows:
        return "The user has not set any budgets yet."
    return json.dumps({"currency": "NPR", "period": "current BS month", "budgets": rows},
                      default=str)


@tool(context=True)
async def set_budget(tool_context: ToolContext, category: Category, amount: Decimal) -> dict | str:
    """Set or change the monthly budget for one category. Ask for the category and the amount
    if the user didn't give both; never invent them.

    Args:
        category: Expense category the budget applies to.
        amount: Monthly budget in Nepali Rupees, greater than 0.
    """
    try:
        body = BudgetIn(amount=amount)
    except ValidationError as e:
        return _error(_validation_text(e))
    with trace_span("tool.set_budget", span_type="TOOL"):
        async with SessionLocal() as s:
            b = await budgets.upsert_budget(s, _user(tool_context), category, body.amount)
            return json.dumps({"budget_set": {"category": b.category,
                                              "monthly_amount": str(b.amount)}})


@tool(context=True)
async def create_expense(
    tool_context: ToolContext,
    amount: Decimal, category: Category,
    merchant: str | None = None, note: str | None = None,
    payment_method: PaymentMethod | None = None,
    expense_date: str | None = None, expense_date_bs: str | None = None,
) -> dict | str:
    """Record a new expense for the user. Ask the user for the amount and category if they
    didn't give them; never invent them.

    Args:
        amount: Amount in Nepali Rupees, greater than 0.
        category: Expense category.
        merchant: Shop, person or service paid (e.g. a family member for a gift).
        note: Short free-text note (e.g. what was bought).
        payment_method: How it was paid.
        expense_date: AD date (YYYY-MM-DD). Give this or expense_date_bs; defaults to today.
        expense_date_bs: Bikram Sambat date (YYYY-MM-DD) when the user gave a BS date.
    """
    if expense_date is None and expense_date_bs is None:
        expense_date = dt.date.today().isoformat()
    try:
        body = ExpenseIn(amount=amount, category=category, merchant=merchant, note=note,
                         payment_method=payment_method, expense_date=expense_date,
                         expense_date_bs=expense_date_bs)
    except ValidationError as e:
        return _error(_validation_text(e))
    with trace_span("tool.create_expense", span_type="TOOL"):
        async with SessionLocal() as s:
            exp = await expenses.create_expense(s, _user(tool_context), body, source="chat")
            return json.dumps({"created": _summary(exp)})


@tool(context=True)
async def update_expense(
    tool_context: ToolContext, expense_id: str,
    amount: Decimal | None = None, category: Category | None = None,
    merchant: str | None = None, note: str | None = None,
    payment_method: PaymentMethod | None = None,
    expense_date: str | None = None, expense_date_bs: str | None = None,
) -> dict | str:
    """Change fields of one of the user's expenses. Only pass the fields that change. The user
    is asked to confirm before anything is saved.

    Args:
        expense_id: Id of the expense, from list_expenses.
        amount: New amount in Nepali Rupees.
        category: New category.
        merchant: New shop or payee name.
        note: New note.
        payment_method: New payment method.
        expense_date: New AD date (YYYY-MM-DD).
        expense_date_bs: New Bikram Sambat date (YYYY-MM-DD).
    """
    if (blocked := _cancelled(tool_context)) is not None:
        return blocked
    changes = {k: v for k, v in locals().items()
               if k not in ("tool_context", "expense_id") and v is not None}
    if not changes:
        return _error("No changes given.")
    try:
        body = ExpenseUpdate(**changes)
    except ValidationError as e:
        return _error(_validation_text(e))
    async with SessionLocal() as s:
        exp = await _owned_expense(s, _user(tool_context), expense_id)
        if exp is None:
            return _error("Expense not found.")
        decision = tool_context.interrupt(CONFIRM, reason={
            "action": "update", "expense": _summary(exp),
            "changes": body.model_dump(mode="json", exclude_unset=True)})
        if decision != "confirm":
            return "The user cancelled; nothing was changed."
        with trace_span("tool.update_expense", span_type="TOOL"):
            exp = await expenses.update_expense(s, exp, body)
            return json.dumps({"updated": _summary(exp)})


@tool(context=True)
async def delete_expense(tool_context: ToolContext, expense_id: str) -> dict | str:
    """Delete one of the user's expenses. The user is asked to confirm before it is deleted.

    Args:
        expense_id: Id of the expense, from list_expenses.
    """
    if (blocked := _cancelled(tool_context)) is not None:
        return blocked
    async with SessionLocal() as s:
        exp = await _owned_expense(s, _user(tool_context), expense_id)
        if exp is None:
            return _error("Expense not found.")
        summary = _summary(exp)
        decision = tool_context.interrupt(CONFIRM, reason={"action": "delete",
                                                           "expense": summary})
        if decision != "confirm":
            return "The user cancelled; nothing was deleted."
        with trace_span("tool.delete_expense", span_type="TOOL"):
            await expenses.delete_expense(s, exp)
            return json.dumps({"deleted": summary})


TOOLS = [aggregate_expenses, list_expenses, search_documents, list_prize_eligible_bills,
         get_budgets, set_budget, create_expense, update_expense, delete_expense]
