import datetime as dt
import uuid
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
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
    full_name: Mapped[str | None] = mapped_column(String(200))
    preferred_language: Mapped[str] = mapped_column(String(2), default="en")
    created_at: Mapped[dt.datetime] = _now()


class IrdCredential(Base):
    __tablename__ = "ird_credentials"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    username_enc: Mapped[str] = mapped_column(Text)
    password_enc: Mapped[str] = mapped_column(Text)
    seller_pan: Mapped[str] = mapped_column(String(20))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


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
    image_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
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
    category: Mapped[str] = mapped_column(String(30), index=True)
    merchant: Mapped[str | None] = mapped_column(String(300))
    note: Mapped[str | None] = mapped_column(Text)
    expense_date_ad: Mapped[dt.date] = mapped_column(Date, index=True)
    expense_date_bs_raw: Mapped[str | None] = mapped_column(String(20))
    payment_method: Mapped[str | None] = mapped_column(String(30))
    source: Mapped[str] = mapped_column(String(10), default="manual")  # manual|chat|bill
    bill_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bills.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = _now()


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


class IssuedInvoice(Base):
    """A bill the user ISSUES (goes to CBMS). is_return=True rows are credit notes."""

    __tablename__ = "issued_invoices"
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = _fk("users")
    is_return: Mapped[bool] = mapped_column(default=False)
    invoice_number: Mapped[str] = mapped_column(String(50))
    invoice_date_bs: Mapped[str] = mapped_column(String(10))
    invoice_date_ad: Mapped[dt.date] = mapped_column(Date)
    fiscal_year: Mapped[str] = mapped_column(String(10))
    buyer_name: Mapped[str] = mapped_column(String(300))
    buyer_pan: Mapped[str | None] = mapped_column(String(20))
    total_sales: Mapped[Decimal] = mapped_column(Money, default=0)
    taxable_sales_vat: Mapped[Decimal] = mapped_column(Money, default=0)
    vat: Mapped[Decimal] = mapped_column(Money, default=0)
    excisable_amount: Mapped[Decimal] = mapped_column(Money, default=0)
    excise: Mapped[Decimal] = mapped_column(Money, default=0)
    taxable_sales_hst: Mapped[Decimal] = mapped_column(Money, default=0)
    hst: Mapped[Decimal] = mapped_column(Money, default=0)
    amount_for_esf: Mapped[Decimal] = mapped_column(Money, default=0)
    esf: Mapped[Decimal] = mapped_column(Money, default=0)
    export_sales: Mapped[Decimal] = mapped_column(Money, default=0)
    tax_exempted_sales: Mapped[Decimal] = mapped_column(Money, default=0)
    # credit-note only
    ref_invoice_number: Mapped[str | None] = mapped_column(String(50))
    credit_note_number: Mapped[str | None] = mapped_column(String(50))
    credit_note_date_bs: Mapped[str | None] = mapped_column(String(10))
    reason_for_return: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[dt.datetime] = _now()
    __table_args__ = (
        UniqueConstraint("user_id", "fiscal_year", "invoice_number", "is_return",
                         name="uq_issued_invoice_number"),
    )


class CbmsSyncQueue(Base):
    __tablename__ = "cbms_sync_queue"
    id: Mapped[uuid.UUID] = _pk()
    issued_invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("issued_invoices.id", ondelete="CASCADE"), unique=True
    )
    endpoint: Mapped[str] = mapped_column(String(10), default="bill")  # bill|billreturn
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=6)
    next_attempt_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    last_response_code: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(String(500))
    synced_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
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
    llm_call_log_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("llm_call_logs.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = _now()
