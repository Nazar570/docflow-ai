from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.persistence.models import (
    AuditLogRecord,
    DecisionRecord,
    DocumentRecord,
    ExtractionRecord,
)
from services.settings import Settings
from services.worker.executor.erp_client import ErpClientError, ErpGateway
from services.worker.extraction.schemas import Invoice
from services.worker.rules.business_rules import (
    DecisionType,
    RuleContext,
    ValidationIssue,
    validate_invoice,
)
from services.worker.rules.states import (
    IllegalStateTransitionError,
    WorkflowState,
    assert_transition_allowed,
)


class ReviewError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ReviewNotFoundError(ReviewError):
    pass


class ReviewConflictError(ReviewError):
    def __init__(self, message: str, *, status: str) -> None:
        super().__init__(message)
        self.status = status


class ReviewValidationError(ReviewError):
    def __init__(self, message: str, *, issues: list[ValidationIssue]) -> None:
        super().__init__(message)
        self.issues = issues


def _audit(
    session: Session,
    *,
    document_id: UUID,
    action: str,
    actor: str,
    details: dict[str, object],
) -> None:
    session.add(
        AuditLogRecord(
            document_id=document_id,
            action=action,
            actor=actor,
            details_json=details,
        ),
    )


def _transition(
    session: Session,
    document: DocumentRecord,
    target: WorkflowState,
    *,
    actor: str,
    details: dict[str, object] | None = None,
) -> None:
    current = WorkflowState(document.status)
    assert_transition_allowed(current, target)
    document.status = target.value
    _audit(
        session,
        document_id=document.id,
        action="state_transition",
        actor=actor,
        details={
            "from_state": current.value,
            "to_state": target.value,
            **(details or {}),
        },
    )


def _latest_extraction(document: DocumentRecord) -> ExtractionRecord | None:
    if not document.extractions:
        return None
    return max(document.extractions, key=lambda item: item.created_at)


def _latest_decision(document: DocumentRecord) -> DecisionRecord | None:
    if not document.decisions:
        return None
    return max(document.decisions, key=lambda item: item.created_at)


def list_needs_review_documents(session: Session) -> list[DocumentRecord]:
    return list(
        session.scalars(
            select(DocumentRecord)
            .where(DocumentRecord.status == WorkflowState.NEEDS_REVIEW.value)
            .order_by(DocumentRecord.created_at.asc()),
        ).all(),
    )


def get_review_document(session: Session, document_id: UUID) -> DocumentRecord:
    document = session.get(DocumentRecord, document_id)
    if document is None:
        raise ReviewNotFoundError("Document not found")
    return document


def build_rule_context(
    *,
    invoice: Invoice,
    settings: Settings,
    erp_client: ErpGateway,
    session: Session,
) -> RuleContext:
    from services.persistence.models import SubmittedInvoiceRecord

    supplier_exists = False
    purchase_order_exists = False
    if invoice.supplier_id:
        try:
            supplier = erp_client.get_supplier(invoice.supplier_id)
        except ErpClientError as exc:
            raise ReviewError(exc.message) from exc
        supplier_exists = supplier is not None
    if invoice.purchase_order_id:
        try:
            purchase_order = erp_client.get_purchase_order(invoice.purchase_order_id)
        except ErpClientError as exc:
            raise ReviewError(exc.message) from exc
        purchase_order_exists = purchase_order is not None
    duplicate = session.scalar(
        select(SubmittedInvoiceRecord).where(
            SubmittedInvoiceRecord.supplier_name == invoice.supplier_name,
            SubmittedInvoiceRecord.invoice_number == invoice.invoice_number,
        ),
    )
    return RuleContext(
        supported_currencies=settings.supported_currencies,
        auto_approval_confidence_threshold=(
            settings.auto_approval_confidence_threshold
        ),
        auto_approval_limit=settings.auto_approval_limit,
        totals_tolerance=settings.totals_tolerance,
        supplier_exists=supplier_exists,
        purchase_order_exists=purchase_order_exists,
        is_duplicate=duplicate is not None,
    )


def approve_review(
    session: Session,
    *,
    document_id: UUID,
    actor: str,
    reason: str,
    settings: Settings,
    erp_client: ErpGateway,
    edited_invoice: Invoice | None = None,
) -> tuple[DocumentRecord, bool]:
    document = session.execute(
        select(DocumentRecord)
        .where(DocumentRecord.id == document_id)
        .with_for_update(),
    ).scalar_one_or_none()
    if document is None:
        raise ReviewNotFoundError("Document not found")

    state = WorkflowState(document.status)
    if state in {WorkflowState.APPROVED, WorkflowState.SUBMITTED}:
        return document, False
    if state == WorkflowState.REJECTED:
        raise ReviewConflictError(
            "Rejected documents cannot be approved",
            status=state.value,
        )
    if state == WorkflowState.FAILED:
        raise ReviewConflictError(
            "Failed documents cannot be approved",
            status=state.value,
        )
    if state != WorkflowState.NEEDS_REVIEW:
        raise ReviewConflictError(
            f"Document status {state.value} is not reviewable",
            status=state.value,
        )

    extraction = _latest_extraction(document)
    before_payload: dict[str, Any] | None = None
    if extraction is not None:
        before_payload = dict(extraction.payload_json)

    if edited_invoice is not None:
        context = build_rule_context(
            invoice=edited_invoice,
            settings=settings,
            erp_client=erp_client,
            session=session,
        )
        validation = validate_invoice(edited_invoice, context)
        if not validation.is_valid:
            raise ReviewValidationError(
                "Edited invoice failed validation",
                issues=validation.issues,
            )
        after_payload = edited_invoice.model_dump(mode="json")
        session.add(
            ExtractionRecord(
                document_id=document.id,
                provider="human_review",
                model_name="human-review",
                model_version=None,
                prompt_version="human-review",
                payload_json=after_payload,
                overall_confidence=edited_invoice.overall_confidence,
                latency_ms=0.0,
                token_usage=None,
                fallback_reason=None,
            ),
        )
        _audit(
            session,
            document_id=document.id,
            action="review_edit_approved",
            actor=actor,
            details={
                "reason": reason,
                "before": before_payload,
                "after": after_payload,
            },
        )
    else:
        if extraction is None:
            raise ReviewValidationError(
                "Document has no extraction to approve",
                issues=[
                    ValidationIssue(
                        code="missing_extraction",
                        message="No extraction payload is available for approval",
                    ),
                ],
            )
        _audit(
            session,
            document_id=document.id,
            action="review_approved",
            actor=actor,
            details={"reason": reason},
        )

    session.add(
        DecisionRecord(
            document_id=document.id,
            decision=DecisionType.HUMAN_APPROVE.value,
            reason=reason,
            actor=actor,
        ),
    )
    try:
        _transition(
            session,
            document,
            WorkflowState.APPROVED,
            actor=actor,
            details={"reason": reason},
        )
    except IllegalStateTransitionError as exc:
        raise ReviewConflictError(
            "Illegal review transition",
            status=document.status,
        ) from exc
    session.flush()
    return document, True


def reject_review(
    session: Session,
    *,
    document_id: UUID,
    actor: str,
    reason: str,
) -> tuple[DocumentRecord, bool]:
    document = session.execute(
        select(DocumentRecord)
        .where(DocumentRecord.id == document_id)
        .with_for_update(),
    ).scalar_one_or_none()
    if document is None:
        raise ReviewNotFoundError("Document not found")

    state = WorkflowState(document.status)
    if state == WorkflowState.REJECTED:
        return document, False
    if state in {
        WorkflowState.SUBMITTED,
        WorkflowState.APPROVED,
        WorkflowState.FAILED,
    }:
        raise ReviewConflictError(
            f"Document status {state.value} cannot be rejected",
            status=state.value,
        )
    if state != WorkflowState.NEEDS_REVIEW:
        raise ReviewConflictError(
            f"Document status {state.value} is not reviewable",
            status=state.value,
        )

    session.add(
        DecisionRecord(
            document_id=document.id,
            decision=DecisionType.REJECTED.value,
            reason=reason,
            actor=actor,
        ),
    )
    _audit(
        session,
        document_id=document.id,
        action="review_rejected",
        actor=actor,
        details={"reason": reason},
    )
    try:
        _transition(
            session,
            document,
            WorkflowState.REJECTED,
            actor=actor,
            details={"reason": reason},
        )
    except IllegalStateTransitionError as exc:
        raise ReviewConflictError(
            "Illegal review transition",
            status=document.status,
        ) from exc
    session.flush()
    return document, True


def resolve_source_path(document: DocumentRecord) -> Path:
    path = Path(document.raw_path)
    if not path.is_file():
        raise ReviewNotFoundError("Source document file not found")
    return path


def review_detail_payload(document: DocumentRecord) -> dict[str, Any]:
    extraction = _latest_extraction(document)
    decision = _latest_decision(document)
    validation_issues: list[dict[str, str]] = []
    if decision is not None and decision.decision == DecisionType.NEEDS_REVIEW.value:
        validation_issues = [
            {"code": "review_reason", "message": decision.reason},
        ]
    else:
        for event in sorted(document.audit_events, key=lambda item: item.created_at):
            if event.action != "validation_completed":
                continue
            codes = event.details_json.get("issue_codes")
            if isinstance(codes, list):
                validation_issues = [
                    {"code": str(code), "message": str(code)} for code in codes
                ]
    return {
        "document_id": document.id,
        "correlation_id": document.correlation_id,
        "status": document.status,
        "source_message_id": document.source_message_id,
        "content_hash": document.content_hash,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
        "extraction": (
            {
                "provider": extraction.provider,
                "model_name": extraction.model_name,
                "prompt_version": extraction.prompt_version,
                "overall_confidence": extraction.overall_confidence,
                "payload": extraction.payload_json,
                "fallback_reason": extraction.fallback_reason,
            }
            if extraction is not None
            else None
        ),
        "decision": (
            {
                "decision": decision.decision,
                "reason": decision.reason,
                "actor": decision.actor,
                "created_at": decision.created_at,
            }
            if decision is not None
            else None
        ),
        "validation_issues": validation_issues,
        "audit_summary": [
            {
                "action": event.action,
                "actor": event.actor,
                "created_at": event.created_at,
                "details": event.details_json,
            }
            for event in sorted(
                document.audit_events,
                key=lambda item: item.created_at,
            )
        ],
        "source_available": Path(document.raw_path).is_file(),
    }
