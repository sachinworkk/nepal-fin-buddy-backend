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
from app.schemas.expenses import ExpenseIn, ExpenseUpdate
from app.services import expenses

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
        merchant: Shop or payee name.
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


TOOLS = [aggregate_expenses, list_expenses, search_documents,
         create_expense, update_expense, delete_expense]
