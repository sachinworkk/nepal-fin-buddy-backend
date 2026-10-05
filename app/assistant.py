"""Hybrid assistant: typed SQL tools over the user's expenses + document search, driven by a
tool-calling loop. user_id is always injected server-side, never taken from model arguments."""

import datetime as dt
import json
import uuid
from decimal import Decimal
from typing import Literal

import structlog
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.categories import Category
from app.core.dates import ad_to_bs
from app.llm import LlmError, chat_with_tools
from app.models import DocumentChunk, Expense
from app.rag import retrieve

log = structlog.get_logger()

MAX_TOOL_ROUNDS = 4
STATEMENT_TIMEOUT = "3s"

SYSTEM_PROMPT = """You are Nepal Fin Buddy's assistant. You help the user understand their \
finances using their expenses, uploaded bills and documents.
Tools:
- aggregate_expenses / list_expenses query the user's recorded expenses. Use them for any \
question about totals, counts, averages, rankings or lists of spending. Never compute totals \
from memory or guess.
- search_documents searches the user's bills and documents for details (items, vendors, VAT).
Rules:
- Ground every factual claim in tool results or the provided context; if they don't contain \
the answer, say you don't have that information instead of guessing.
- When you use information from a document chunk, cite it inline right after the claim like \
[title | chunk N] using the chunk's exact title and number. Only cite chunks that actually \
appear in the context or search results; never invent citations.
- Amounts are in Nepali Rupees (Rs.). Format them like Rs. 1,234.56.
- Dates may be in Bikram Sambat (BS) as well as Gregorian (AD); tool date arguments are \
always AD (YYYY-MM-DD). Convert BS months/dates to AD yourself using today's date below.
- Reply in the language the user wrote in (English or Nepali).
- VAT amounts on bills are 13% of the taxable amount in Nepal; do not "correct" the data.
- Be concise."""


class AggregateArgs(BaseModel):
    metric: Literal["sum", "count", "avg"] = "sum"
    group_by: Literal["none", "category", "merchant", "month"] = "none"
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    category: Category | None = None
    merchant: str | None = Field(default=None, max_length=100)
    top_n: int = Field(default=10, ge=1, le=20)


class ListArgs(BaseModel):
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    category: Category | None = None
    merchant: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=20, ge=1, le=50)


class SearchArgs(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    document_id: uuid.UUID | None = None


def _tool(name: str, description: str, model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()
    schema.pop("title", None)
    return {"type": "function",
            "function": {"name": name, "description": description, "parameters": schema}}


TOOLS = [
    _tool("aggregate_expenses",
          "Sum, count or average the user's expenses, optionally grouped and filtered. "
          "Dates are AD (YYYY-MM-DD), inclusive.", AggregateArgs),
    _tool("list_expenses", "List individual expenses, newest first (max 50).", ListArgs),
    _tool("search_documents",
          "Search the user's bills and documents; returns text chunks with titles.", SearchArgs),
]


def _filters(a: AggregateArgs | ListArgs, user_id: uuid.UUID) -> list:
    conds = [Expense.user_id == user_id]
    if a.date_from:
        conds.append(Expense.expense_date_ad >= a.date_from)
    if a.date_to:
        conds.append(Expense.expense_date_ad <= a.date_to)
    if a.category:
        conds.append(Expense.category == a.category.value)
    if a.merchant:
        conds.append(Expense.merchant.ilike(f"%{a.merchant}%"))
    return conds


def _jsonable(v):
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, dt.date):
        return v.isoformat()
    return v


async def _read_only_rows(session: AsyncSession, q):
    """Run `q` on a separate connection in a READ ONLY transaction with a statement timeout,
    so the request session (and its loaded ORM objects) is never touched."""
    async with AsyncSession(bind=session.bind, expire_on_commit=False) as ro:
        await ro.execute(text("SET TRANSACTION READ ONLY"))  # must be the first statement
        await ro.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'"))
        return (await ro.execute(q)).all()


async def aggregate_expenses(session: AsyncSession, user_id: uuid.UUID,
                             a: AggregateArgs) -> dict:
    value = {"sum": func.coalesce(func.sum(Expense.amount), 0),
             "count": func.count(Expense.id),
             "avg": func.coalesce(func.avg(Expense.amount), 0)}[a.metric]
    group_col = {"category": Expense.category, "merchant": Expense.merchant,
                 "month": func.to_char(Expense.expense_date_ad, "YYYY-MM")}.get(a.group_by)
    cols = [value.label("value")]
    if group_col is not None:
        cols.insert(0, group_col.label("group"))
    q = select(*cols).where(*_filters(a, user_id))
    if group_col is not None:
        q = q.group_by(group_col)
        q = q.order_by(group_col) if a.group_by == "month" else q.order_by(value.desc())
        q = q.limit(a.top_n)
    rows = await _read_only_rows(session, q)
    if group_col is None:
        return {"metric": a.metric, "value": _jsonable(rows[0].value), "currency": "NPR"}
    return {"metric": a.metric, "group_by": a.group_by, "currency": "NPR",
            "rows": [{"group": r.group, "value": _jsonable(r.value)} for r in rows]}


async def list_expenses(session: AsyncSession, user_id: uuid.UUID, a: ListArgs) -> dict:
    q = (select(Expense.expense_date_ad, Expense.amount, Expense.category, Expense.merchant,
                Expense.note, Expense.expense_date_bs_raw)
         .where(*_filters(a, user_id))
         .order_by(Expense.expense_date_ad.desc(), Expense.created_at.desc())
         .limit(a.limit))
    rows = await _read_only_rows(session, q)
    return {"currency": "NPR", "expenses": [
        {"date": _jsonable(r.expense_date_ad), "date_bs": r.expense_date_bs_raw,
         "amount": _jsonable(r.amount), "category": r.category, "merchant": r.merchant,
         "note": r.note} for r in rows]}


def _chunk_text(chunks: list[DocumentChunk]) -> str:
    return "\n\n---\n\n".join(
        f"[{c.meta.get('title', 'document')} | chunk {c.chunk_index}]\n{c.content}"
        for c in chunks)


def system_messages(today: dt.date) -> list[dict]:
    bs = ad_to_bs(today)
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system",
             "content": f"Today is {today.isoformat()} AD ({bs.year}-{bs.month:02d}-"
                        f"{bs.day:02d} BS)."}]


async def _run_tool(session: AsyncSession, user_id: uuid.UUID, name: str, raw_args: str,
                    document_id: uuid.UUID | None,
                    chunks: list[DocumentChunk]) -> tuple[str, dict]:
    """Execute one tool call. Returns (result_text_for_model, parsed_args_for_audit).
    Validation problems are returned to the model as text so it can retry."""
    try:
        args = json.loads(raw_args or "{}")
        if not isinstance(args, dict):
            raise ValueError("arguments must be a JSON object")
        args.pop("user_id", None)  # never trusted; scoping is server-side
        if name == "aggregate_expenses":
            result = await aggregate_expenses(session, user_id, AggregateArgs(**args))
        elif name == "list_expenses":
            result = await list_expenses(session, user_id, ListArgs(**args))
        elif name == "search_documents":
            sa = SearchArgs(**args)
            # A scoped chat stays scoped; the model can't widen it.
            found = await retrieve(session, user_id, sa.query, top_k=6,
                                   document_id=document_id or sa.document_id)
            known = {c.id for c in chunks}
            chunks.extend(c for c in found if c.id not in known)
            return (_chunk_text(found) or "No matching documents."), args
        else:
            return f"Unknown tool {name!r}.", {}
        return json.dumps(result), args
    except (ValidationError, ValueError) as e:
        return f"Invalid arguments: {e}", {}
    except LlmError as e:
        return f"Tool failed: {e}", {}
    except Exception as e:  # DB timeout etc. must not 500 the chat
        log.warning("assistant.tool_failed", tool=name, error=str(e))
        return "Tool failed; answer without it or say you couldn't look that up.", {}


async def run_agent(session: AsyncSession, user_id: uuid.UUID, messages: list[dict],
                    chunks: list[DocumentChunk], document_id: uuid.UUID | None = None,
                    ) -> tuple[str, list[dict]]:
    """Tool-calling loop. `chunks` is mutated with chunks found via search_documents so the
    caller can validate citations. Returns (answer, tool_audit) where tool_audit is a list of
    {"name", "args", "result"} for persistence."""
    audit: list[dict] = []
    for _ in range(MAX_TOOL_ROUNDS):
        msg = await chat_with_tools(session, user_id, messages, TOOLS)
        calls = msg.get("tool_calls") or []
        if not calls:
            return msg.get("content") or "", audit
        messages.append({"role": "assistant", "content": msg.get("content") or "",
                         "tool_calls": calls})
        for call in calls:
            fn = call.get("function") or {}
            name = fn.get("name", "")
            result, args = await _run_tool(session, user_id, name, fn.get("arguments", ""),
                                           document_id, chunks)
            audit.append({"name": name, "args": args, "result": result[:2000]})
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                             "content": result})
    # Out of rounds: force a final answer without offering tools.
    msg = await chat_with_tools(session, user_id, messages, None)
    return msg.get("content") or "", audit
