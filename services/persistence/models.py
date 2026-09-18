from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid


class Base(DeclarativeBase):
    pass


class DocumentRecord(Base):
    __tablename__ = "documents"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )
    source_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_path: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    correlation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    extractions: Mapped[list["ExtractionRecord"]] = relationship(
        back_populates="document"
    )
    decisions: Mapped[list["DecisionRecord"]] = relationship(back_populates="document")
    audit_events: Mapped[list["AuditLogRecord"]] = relationship(
        back_populates="document",
    )


class ExtractionRecord(Base):
    __tablename__ = "extractions"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    document_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    overall_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    token_usage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fallback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    document: Mapped[DocumentRecord] = relationship(back_populates="extractions")


class DecisionRecord(Base):
    __tablename__ = "decisions"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    document_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id"),
        nullable=False,
    )
    decision: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    document: Mapped[DocumentRecord] = relationship(back_populates="decisions")


class AuditLogRecord(Base):
    __tablename__ = "audit_log"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    document_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    document: Mapped[DocumentRecord] = relationship(back_populates="audit_events")


class SubmittedInvoiceRecord(Base):
    __tablename__ = "submitted_invoices"
    __table_args__ = (
        UniqueConstraint(
            "supplier_name",
            "invoice_number",
            name="uq_submitted_supplier_invoice",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    document_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("documents.id"),
        nullable=False,
    )
    supplier_name: Mapped[str] = mapped_column(String(255), nullable=False)
    invoice_number: Mapped[str] = mapped_column(String(128), nullable=False)
    erp_invoice_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
