import datetime as dt
import uuid
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

EMBED_DIM = 1024
Money = Numeric(14, 2)
JSONType = JSON().with_variant(JSONB(), "postgresql")


def _pk():
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _fk(table: str, nullable: bool = False, index: bool = True):
    return mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{table}.id", ondelete="CASCADE"),
        nullable=nullable, index=index,
    )


def _now():
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200))
    preferred_language: Mapped[str] = mapped_column(String(2), default="en")
    created_at: Mapped[dt.datetime] = _now()


class Category(Base):
    """Expense category lookup; the slug is the natural key the API already exposes."""

    __tablename__ = "categories"
    slug: Mapped[str] = mapped_column(String(30), primary_key=True)
    label_en: Mapped[str] = mapped_column(String(50))
    label_ne: Mapped[str] = mapped_column(String(50))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class PaymentMethod(Base):
    """Payment method lookup; `ird_code` is the value the IRD prize portal expects, if any."""

    __tablename__ = "payment_methods"
    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    label_en: Mapped[str] = mapped_column(String(50))
    label_ne: Mapped[str] = mapped_column(String(50))
    ird_code: Mapped[str | None] = mapped_column(String(10))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Bill(Base):
    """A bill the user RECEIVED (snap-to-ledger)."""

    __tablename__ = "bills"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _fk("users")
    vendor_name: Mapped[str | None] = mapped_column(String(300))
    vendor_pan: Mapped[str | None] = mapped_column(String(20))
    bill_number: Mapped[str | None] = mapped_column(String(100))
    bill_date_ad: Mapped[dt.date | None] = mapped_column(Date, index=True)
    bill_date_bs_raw: Mapped[str | None] = mapped_column(String(20))
    subtotal: Mapped[Decimal | None] = mapped_column(Money)
    discount: Mapped[Decimal | None] = mapped_column(Money)
    taxable_amount: Mapped[Decimal | None] = mapped_column(Money)
    vat_amount: Mapped[Decimal | None] = mapped_column(Money)
    total_amount: Mapped[Decimal | None] = mapped_column(Money)
    image_path: Mapped[str | None] = mapped_column(String(500))
    payment_method: Mapped[str | None] = mapped_column(
        String(10), ForeignKey("payment_methods.code")
    )
    mime_type: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="extracted")  # extracted|confirmed
    extraction_confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[dt.datetime] = _now()
    items: Mapped[list["BillItem"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class BillItem(Base):
    __tablename__ = "bill_items"
    id: Mapped[uuid.UUID] = _pk()
    bill_id: Mapped[uuid.UUID] = _fk("bills")
    description: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    unit_price: Mapped[Decimal | None] = mapped_column(Money)
    amount: Mapped[Decimal | None] = mapped_column(Money)


class Extraction(Base):
    """One row per extractor run; raw output is kept for benchmarking."""

    __tablename__ = "extractions"
    id: Mapped[uuid.UUID] = _pk()
    bill_id: Mapped[uuid.UUID] = _fk("bills")
    extractor: Mapped[str] = mapped_column(String(50))
    model: Mapped[str | None] = mapped_column(String(200))
    prompt_version: Mapped[str | None] = mapped_column(String(20))
    structured: Mapped[dict | None] = mapped_column(JSONType)
    raw_output: Mapped[str | None] = mapped_column(Text)
    ocr_text: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    field_confidences: Mapped[dict | None] = mapped_column(JSONType)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    llm_call_log_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("llm_call_logs.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = _now()


class Expense(Base):
    __tablename__ = "expenses"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _fk("users")
    amount: Mapped[Decimal] = mapped_column(Money)
    category: Mapped[str] = mapped_column(
        String(30), ForeignKey("categories.slug"), index=True
    )
    merchant: Mapped[str | None] = mapped_column(String(300))
    note: Mapped[str | None] = mapped_column(Text)
    expense_date_ad: Mapped[dt.date] = mapped_column(Date, index=True)
    expense_date_bs_raw: Mapped[str | None] = mapped_column(String(20))
    payment_method: Mapped[str | None] = mapped_column(
        String(10), ForeignKey("payment_methods.code")
    )
    source: Mapped[str] = mapped_column(String(10), default="manual")  # manual|chat|bill
    bill_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bills.id", ondelete="SET NULL")
    )
    bill: Mapped[Bill | None] = relationship(lazy="selectin")
    created_at: Mapped[dt.datetime] = _now()
    # A bill produces at most one expense: the confirm step, or a manual expense entered
    # with bill details (an image-less bill created alongside it).
    __table_args__ = (
        Index("uq_expenses_bill_id", "bill_id", unique=True,
              postgresql_where=bill_id.is_not(None)),
    )


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _fk("users")
    kind: Mapped[str] = mapped_column(String(20))  # bill|statement|note|pdf
    title: Mapped[str] = mapped_column(String(300))
    file_path: Mapped[str | None] = mapped_column(String(500))
    mime_type: Mapped[str | None] = mapped_column(String(100))
    bill_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bills.id", ondelete="SET NULL")
    )
    text_content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = _now()


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    id: Mapped[uuid.UUID] = _pk()
    document_id: Mapped[uuid.UUID] = _fk("documents")
    user_id: Mapped[uuid.UUID] = _fk("users")
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding = mapped_column(Vector(EMBED_DIM))
    embedding_model: Mapped[str] = mapped_column(String(200))
    meta: Mapped[dict | None] = mapped_column("metadata", JSONType)
    # Maintained by trigger trg_document_chunks_tsv (alembic 0002); used for keyword retrieval.
    content_tsv: Mapped[object] = mapped_column(TSVECTOR, nullable=True)
    __table_args__ = (
        Index(
            "ix_document_chunks_embedding", "embedding", postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class PrizeEnrollment(Base):
    """A cash bill a customer submitted to the IRD prize lottery (prize.ird.gov.np)."""

    __tablename__ = "prize_enrollments"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _fk("users")
    bill_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bills.id", ondelete="SET NULL")
    )
    seller_pan_no: Mapped[str] = mapped_column(String(9))
    bill_number: Mapped[str] = mapped_column(String(40))
    bill_date: Mapped[dt.date] = mapped_column(Date)
    billed_total_amount: Mapped[Decimal] = mapped_column(Money)
    payment_method: Mapped[str] = mapped_column(String(10), default="Cash")
    name: Mapped[str] = mapped_column(String(120))
    address: Mapped[str] = mapped_column(String(200))
    mobile_number: Mapped[str] = mapped_column(String(10))
    company_website: Mapped[str | None] = mapped_column(String(200))
    # submitted|failed|handed_off (prefilled for the user to submit on the IRD portal)|dismissed
    status: Mapped[str] = mapped_column(String(12), default="submitted")
    prize_coupon_number: Mapped[str | None] = mapped_column(String(12))
    verification_id: Mapped[str | None] = mapped_column(String(30))
    duplicate: Mapped[bool] = mapped_column(default=False)
    ird_response: Mapped[dict | None] = mapped_column(JSONType)
    last_error: Mapped[str | None] = mapped_column(String(500))
    form_started_at: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[dt.datetime] = _now()
    __table_args__ = (
        Index("ix_prize_enrollments_user_bill", "user_id", "seller_pan_no",
              "bill_number", "bill_date"),
    )


class LlmCallLog(Base):
    """One row per HTTP attempt to OpenRouter. Never stores prompt text."""

    __tablename__ = "llm_call_logs"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    purpose: Mapped[str] = mapped_column(String(40), index=True)
    model: Mapped[str] = mapped_column(String(200), index=True)
    provider: Mapped[str | None] = mapped_column(String(100))
    generation_id: Mapped[str | None] = mapped_column(String(100))
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    cost_source: Mapped[str | None] = mapped_column(String(12))  # openrouter|estimated
    status: Mapped[str] = mapped_column(String(10))  # ok|error
    http_status: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String(500))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[dt.datetime] = _now()


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _fk("users")
    kind: Mapped[str] = mapped_column(String(20))  # expense_entry|assistant
    title: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[dt.datetime] = _now()


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[uuid.UUID] = _pk()
    session_id: Mapped[uuid.UUID] = _fk("chat_sessions")
    role: Mapped[str] = mapped_column(String(12))  # user|assistant|tool
    content: Mapped[str] = mapped_column(Text, default="")
    tool_name: Mapped[str | None] = mapped_column(String(50))
    tool_args: Mapped[dict | None] = mapped_column(JSONType)
    citations: Mapped[list | None] = mapped_column(JSONType)
    # Edits/deletes the agent paused on, awaiting the user's confirm/cancel.
    pending_actions: Mapped[list | None] = mapped_column(JSONType)
    llm_call_log_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("llm_call_logs.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = _now()


class AgentStorage(Base):
    """Key/value blobs for the Strands agent's session snapshots (one chat session each)."""

    __tablename__ = "agent_storage"
    key: Mapped[str] = mapped_column(String(500), primary_key=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Budget(Base):
    """A monthly spending limit for one category; measured against the current BS month."""

    __tablename__ = "budgets"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _fk("users")
    category: Mapped[str] = mapped_column(String(30), ForeignKey("categories.slug"))
    amount: Mapped[Decimal] = mapped_column(Money)
    created_at: Mapped[dt.datetime] = _now()
    __table_args__ = (UniqueConstraint("user_id", "category", name="uq_budget_user_category"),)
