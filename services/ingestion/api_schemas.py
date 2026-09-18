from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from services.worker.extraction.schemas import Invoice


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str


class AuditEventSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    actor: str
    created_at: datetime
    details: dict[str, Any]


class DecisionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str
    reason: str
    actor: str
    created_at: datetime


class FailureSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error_class: str
    retry_count: int
    dlq_present: bool = False


class DocumentAcceptedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    correlation_id: UUID
    status: str
    idempotent_replay: bool = False


class DocumentStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    correlation_id: UUID
    status: str
    source_message_id: str
    content_hash: str
    created_at: datetime
    updated_at: datetime
    retry_count: int = 0
    last_error_class: str | None = None
    failure: FailureSummary | None = None
    decision: DecisionSummary | None = None
    audit_summary: list[AuditEventSummary] = Field(default_factory=list)


class DlqEntryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    correlation_id: str
    retry_count: int
    error_class: str
    error_message: str
    failed_at: str


class DlqListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    depth: int
    entries: list[DlqEntryResponse]


class ReviewListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    correlation_id: UUID
    status: str
    source_message_id: str
    created_at: datetime
    updated_at: datetime
    overall_confidence: float | None = None
    decision_reason: str | None = None


class ReviewListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ReviewListItem]


class ExtractionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model_name: str
    prompt_version: str
    overall_confidence: float
    payload: dict[str, Any]
    fallback_reason: str | None = None


class ValidationIssueSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class ReviewDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    correlation_id: UUID
    status: str
    source_message_id: str
    content_hash: str
    created_at: datetime
    updated_at: datetime
    extraction: ExtractionSummary | None = None
    decision: DecisionSummary | None = None
    validation_issues: list[ValidationIssueSummary] = Field(default_factory=list)
    audit_summary: list[AuditEventSummary] = Field(default_factory=list)
    source_available: bool = False


class ReviewActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    edited_invoice: Invoice | None = None


class ReviewActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    correlation_id: UUID
    status: str
    resumed: bool = False
    idempotent_replay: bool = False
