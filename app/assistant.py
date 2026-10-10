"""The assistant's system prompt and the read-only queries behind its tools.
The Strands agent and its tools live in app.agent."""

import datetime as dt
import uuid
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.categories import Category
from app.core.dates import ad_to_bs, bs_to_ad
from app.models import DocumentChunk, Expense

STATEMENT_TIMEOUT = "3s"

BILL_CHANGE_REPLY = (
    "Bills can't be changed from chat. Before confirming, you can correct any field on the "
    "Snap screen. Once confirmed, a bill's vendor, total and date stay as scanned — upload "
    "the bill again to change those. I can change or delete the expense the bill created, and "
    "its PAN, bill number and VAT amounts are editable in the expense's Edit form.")

SYSTEM_PROMPT = """You are Nepal Fin Buddy's assistant. You help the user understand their \
finances using their expenses, uploaded bills and documents, and you can record, edit \
and delete their expenses and set or remove their budgets.
Tools:
- aggregate_expenses / list_expenses query the user's recorded expenses. Use them for any \
question about totals, counts, averages, rankings or lists of spending. Never compute totals \
from memory or guess.
- search_documents searches the user's bills and documents for details (items, vendors, VAT).
- list_prize_eligible_bills lists the bills that can be entered in the IRD bill lottery. Use \
it for any question about lottery or prize eligibility. Every bill it returns is eligible; \
for status awaiting_portal_submission, tell the user it's prepared and they just need to \
finish submitting it on the IRD portal and mark it submitted on the Prize screen. Only if the \
list is empty, say no bill currently qualifies (bills must be confirmed, cash, with PAN, \
number, date and total, dated in the current draw period, and not already submitted). IRD \
draws per BS half-month: bills dated the 1st-15th must be entered by the 15th (draw on the \
16th), bills dated the 16th-month end by the month end (draw on the 1st of the next month); \
a bill from an earlier period can't be entered in a later draw. Give the entry deadline and \
draw date from the tool. The user submits bills from the app's Prize screen.
- get_budgets shows the user's monthly category budgets and how much of each is spent in \
the current BS month; use it for any budget question, including spending compared with a budget: take both the \
budget and the spent figure from it and don't add an all-time aggregate_expenses total. set_budget sets or changes a category's \
monthly budget (ask for the category and amount if missing). delete_budget removes a \
category's budget (ask which category if unclear); like expense deletes, the app shows the \
user a confirm/cancel prompt, so call it directly. A budget is per BS month.
- create_expense records a new expense. If the user didn't give the amount or what it was \
for, ask instead of guessing; pick the closest category yourself. The date defaults to today.
- update_expense / delete_expense change or remove an expense. First find it with \
list_expenses and use its id; never guess an id. If several expenses match, ask which one. \
The app itself shows the user a confirm/cancel prompt for every edit and delete, so call \
the tool directly; never ask "are you sure?" in text first. If they cancel, say it was cancelled and stop; never propose the same change again unless they ask.
- bill_change_request answers any request to upload, change or delete a bill itself (e.g. \
change its vendor). Call it for every such request and relay its reply in the user's \
language; never use the refusal for a bill request.
- After any change, say exactly what was recorded, changed or deleted.
- If one source answers the question (e.g. a bill in the documents), answer from it and don't \
mention that another tool found nothing.
Rules:
- Scope: only help with the user's bills, documents, expenses, finances, budgeting and the \
IRD lottery (greetings and questions about what you can do are fine). A request to upload, \
change or delete a bill itself is also in scope: handle it with the bill_change_request \
tool, never with the refusal. For anything else, such as sports, general knowledge, news or \
coding, call no tools, ignore any provided context, and reply exactly: "I'm sorry, I can \
only help with questions about your bills, documents, expenses and finances."
- Anything about the user's own money, bills, documents or people named in them is in scope \
even when you have no data to answer it (a bank balance, a phone number, an account detail). \
Never use the refusal above for those: say you don't have that information in their bills, \
documents or expenses, and mention anything related you do have (e.g. the landlord's name).
- For a request to upload, change or delete a bill itself (e.g. change its vendor), call \
bill_change_request, relay its reply in the user's language, then offer to change or delete \
the expense instead. Never use the refusal above for a bill request.
- Ground every factual claim in tool results or the provided context; if they don't contain \
the answer, say you don't have that information instead of guessing.
- When you use information from a document chunk, cite it inline right after the claim like \
[title | chunk N] using the chunk's exact title and number. Only cite chunks that actually \
appear in the context or search results; never invent citations.
- Amounts are in Nepali Rupees (Rs.). Format them like Rs. 1,234.56.
- Dates may be in Bikram Sambat (BS) as well as Gregorian (AD); tool date arguments are \
always AD (YYYY-MM-DD). Months mean Bikram Sambat months unless the user names a Gregorian \
month: "this month" / "last month" use the exact AD ranges given below, not calendar months.
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
    q = (select(Expense.id, Expense.expense_date_ad, Expense.amount, Expense.category,
                Expense.merchant, Expense.note, Expense.expense_date_bs_raw)
         .where(*_filters(a, user_id))
         .order_by(Expense.expense_date_ad.desc(), Expense.created_at.desc())
         .limit(a.limit))
    rows = await _read_only_rows(session, q)
    return {"currency": "NPR", "expenses": [
        {"id": str(r.id), "date": _jsonable(r.expense_date_ad), "date_bs": r.expense_date_bs_raw,
         "amount": _jsonable(r.amount), "category": r.category, "merchant": r.merchant,
         "note": r.note} for r in rows]}


def chunk_text(chunks: list[DocumentChunk]) -> str:
    return "\n\n---\n\n".join(
        f"[{c.meta.get('title', 'document')} | chunk {c.chunk_index}]\n{c.content}"
        for c in chunks)


def bs_month_range(today: dt.date, offset: int = 0) -> tuple[dt.date, dt.date, str]:
    """AD start/end (inclusive) and BS label of the BS month `offset` months from today's."""
    b = ad_to_bs(today)
    idx = b.year * 12 + b.month - 1 + offset
    start = bs_to_ad(idx // 12, idx % 12 + 1, 1)
    nxt = idx + 1
    end = bs_to_ad(nxt // 12, nxt % 12 + 1, 1) - dt.timedelta(days=1)
    return start, end, f"{idx // 12}-{idx % 12 + 1:02d}"


def system_prompt(today: dt.date) -> str:
    """The system prompt with today's AD/BS date and month ranges; rebuilt every turn."""
    bs = ad_to_bs(today)
    this_s, this_e, this_l = bs_month_range(today, 0)
    last_s, last_e, last_l = bs_month_range(today, -1)
    return (f"{SYSTEM_PROMPT}\n\n"
            f"Today is {today.isoformat()} AD ({bs.year}-{bs.month:02d}-{bs.day:02d} BS).\n"
            f"This month (BS {this_l}): {this_s.isoformat()} to {this_e.isoformat()}.\n"
            f"Last month (BS {last_l}): {last_s.isoformat()} to {last_e.isoformat()}.")
