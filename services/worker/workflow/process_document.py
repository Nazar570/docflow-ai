import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.observability import metrics as obs_metrics
from services.observability.logging import log_event
from services.observability.tracing import safe_span_attributes, start_span
from services.persistence.models import (
    AuditLogRecord,
    DecisionRecord,
    DocumentRecord,
    ExtractionRecord,
    SubmittedInvoiceRecord,
)
from services.settings import Settings
from services.worker.executor.erp_client import (
    ErpClientError,
    ErpGateway,
    ErpInvoiceCreateRequest,
    build_erp_idempotency_key,
)
from services.worker.extraction.pdf_text import (
    PdfTextExtractionError,
    extract_text_from_pdf,
)
from services.worker.extraction.schemas import Invoice
from services.worker.providers.contract import ExtractionRequest, LLMProvider
from services.worker.providers.errors import (
    NonRecoverableProviderError,
    ProviderOutputError,
)
from services.worker.rules.business_rules import (
    DecisionType,
    RuleContext,
    decide_workflow,
)
from services.worker.rules.states import (
    TERMINAL_STATES,
    WORKER_NOOP_STATES,
    IllegalStateTransitionError,
    WorkflowState,
    assert_transition_allowed,
)
from services.worker.transient import TransientWorkerError, classify_transient_error

logger = logging.getLogger(__name__)


class WorkflowError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _workflow_duration_seconds(document: DocumentRecord) -> float:
    created = document.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return max((datetime.now(UTC) - created).total_seconds(), 0.0)


def _record_outcome(document: DocumentRecord, final_state: str) -> None:
    obs_metrics.record_workflow_outcome(
        final_state=final_state,
        duration_seconds=_workflow_duration_seconds(document),
    )


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


def queue_document(session: Session, document: DocumentRecord) -> DocumentRecord:
    _transition(
        session,
        document,
        WorkflowState.QUEUED,
        actor="system",
    )
    session.flush()
    return document


def record_retry_metadata(
    session: Session,
    document: DocumentRecord,
    *,
    retry_count: int,
    error_class: str,
) -> None:
    document.retry_count = retry_count
    document.last_error_class = error_class
    _audit(
        session,
        document_id=document.id,
        action="worker_retry_scheduled",
        actor="system",
        details={
            "retry_count": retry_count,
            "error_class": error_class,
        },
    )
    session.flush()


def mark_retry_exhausted(
    session: Session,
    document: DocumentRecord,
    *,
    retry_count: int,
    error_class: str,
) -> DocumentRecord:
    state = WorkflowState(document.status)
    if state in TERMINAL_STATES:
        document.retry_count = retry_count
        document.last_error_class = error_class
        session.flush()
        return document
    document.retry_count = retry_count
    document.last_error_class = error_class
    _transition(
        session,
        document,
        WorkflowState.FAILED,
        actor="system",
        details={
            "error_class": error_class,
            "retry_count": retry_count,
            "outcome": "retry_exhausted",
        },
    )
    _audit(
        session,
        document_id=document.id,
        action="retry_exhausted",
        actor="system",
        details={
            "error_class": error_class,
            "retry_count": retry_count,
        },
    )
    session.flush()
    _record_outcome(document, "failed")
    return document


def _latest_extraction(document: DocumentRecord) -> ExtractionRecord | None:
    if not document.extractions:
        return None
    return max(document.extractions, key=lambda item: item.created_at)


def _latest_decision(document: DocumentRecord) -> DecisionRecord | None:
    if not document.decisions:
        return None
    return max(document.decisions, key=lambda item: item.created_at)


def _submission_for_document(
    session: Session,
    document: DocumentRecord,
) -> SubmittedInvoiceRecord | None:
    return session.scalar(
        select(SubmittedInvoiceRecord).where(
            SubmittedInvoiceRecord.document_id == document.id,
        ),
    )


def _submit_approved_invoice(
    session: Session,
    *,
    document: DocumentRecord,
    invoice: Invoice,
    erp_client: ErpGateway,
    provider_name: str,
) -> DocumentRecord:
    existing = _submission_for_document(session, document)
    if existing is not None:
        if WorkflowState(document.status) == WorkflowState.APPROVED:
            _transition(
                session,
                document,
                WorkflowState.SUBMITTED,
                actor="system",
                details={"idempotent": True},
            )
            session.flush()
        return document

    if invoice.supplier_id is None:
        _transition(
            session,
            document,
            WorkflowState.FAILED,
            actor="system",
            details={"error_class": "missing_supplier_id"},
        )
        raise WorkflowError("Approved invoice is missing supplier_id")

    erp_key = build_erp_idempotency_key(
        document_id=str(document.id),
        invoice_number=invoice.invoice_number,
    )
    started = datetime.now(UTC)
    try:
        with start_span(
            "erp.submit_invoice",
            attributes=safe_span_attributes(
                document_id=str(document.id),
                correlation_id=str(document.correlation_id),
                workflow_state=document.status,
                provider=provider_name,
                outcome="pending",
            ),
        ):
            erp_invoice = erp_client.create_invoice(
                ErpInvoiceCreateRequest(
                    supplier_id=invoice.supplier_id,
                    purchase_order_id=invoice.purchase_order_id,
                    invoice_number=invoice.invoice_number,
                    invoice_date=invoice.invoice_date.isoformat(),
                    currency=invoice.currency,
                    total_amount=invoice.total_amount,
                    line_items=[
                        item.model_dump(mode="json") for item in invoice.line_items
                    ],
                ),
                idempotency_key=erp_key,
            )
    except ErpClientError as exc:
        elapsed = (datetime.now(UTC) - started).total_seconds()
        obs_metrics.record_erp_submission(
            outcome="failure",
            duration_seconds=elapsed,
        )
        transient = classify_transient_error(exc)
        if transient is not None:
            raise transient from exc
        _transition(
            session,
            document,
            WorkflowState.FAILED,
            actor="system",
            details={"error_class": "erp_submission_error"},
        )
        _record_outcome(document, "failed")
        log_event(
            logger,
            service="worker",
            event="document_failed",
            document_id=str(document.id),
            correlation_id=str(document.correlation_id),
            workflow_state="failed",
            error_class="erp_submission_error",
            outcome="failed",
        )
        raise WorkflowError(exc.message) from exc

    elapsed = (datetime.now(UTC) - started).total_seconds()
    obs_metrics.record_erp_submission(outcome="success", duration_seconds=elapsed)
    duplicate_business = session.scalar(
        select(SubmittedInvoiceRecord).where(
            SubmittedInvoiceRecord.supplier_name == invoice.supplier_name,
            SubmittedInvoiceRecord.invoice_number == invoice.invoice_number,
        ),
    )
    if duplicate_business is None:
        session.add(
            SubmittedInvoiceRecord(
                document_id=document.id,
                supplier_name=invoice.supplier_name,
                invoice_number=invoice.invoice_number,
                erp_invoice_id=erp_invoice.invoice_id,
            ),
        )
    _audit(
        session,
        document_id=document.id,
        action="erp_submitted",
        actor="system",
        details={
            "erp_invoice_id": erp_invoice.invoice_id,
            "idempotency_key_present": True,
        },
    )
    _transition(
        session,
        document,
        WorkflowState.SUBMITTED,
        actor="system",
    )
    _record_outcome(document, "submitted")
    log_event(
        logger,
        service="worker",
        event="document_submitted",
        document_id=str(document.id),
        correlation_id=str(document.correlation_id),
        workflow_state=document.status,
        provider=provider_name,
        outcome="submitted",
    )
    session.flush()
    return document


def _decide_and_maybe_submit(
    session: Session,
    *,
    document: DocumentRecord,
    invoice: Invoice,
    settings: Settings,
    erp_client: ErpGateway,
    provider_name: str,
) -> DocumentRecord:
    existing_decision = _latest_decision(document)
    if existing_decision is not None:
        if existing_decision.decision == DecisionType.NEEDS_REVIEW.value:
            if WorkflowState(document.status) == WorkflowState.EXTRACTED:
                _transition(
                    session,
                    document,
                    WorkflowState.NEEDS_REVIEW,
                    actor="system",
                    details={"reason_code": "validation_review", "idempotent": True},
                )
                session.flush()
            return document
        if existing_decision.decision == DecisionType.AUTO_APPROVE.value:
            if WorkflowState(document.status) == WorkflowState.EXTRACTED:
                _transition(
                    session,
                    document,
                    WorkflowState.APPROVED,
                    actor="system",
                    details={"idempotent": True},
                )
                session.flush()
            return _submit_approved_invoice(
                session,
                document=document,
                invoice=invoice,
                erp_client=erp_client,
                provider_name=provider_name,
            )

    supplier_exists = False
    purchase_order_exists = False
    if invoice.supplier_id:
        try:
            supplier = erp_client.get_supplier(invoice.supplier_id)
        except ErpClientError as exc:
            transient = classify_transient_error(exc)
            if transient is not None:
                raise transient from exc
            raise WorkflowError(exc.message) from exc
        supplier_exists = supplier is not None
    duplicate = session.scalar(
        select(SubmittedInvoiceRecord).where(
            SubmittedInvoiceRecord.supplier_name == invoice.supplier_name,
            SubmittedInvoiceRecord.invoice_number == invoice.invoice_number,
        ),
    )
    if invoice.purchase_order_id:
        try:
            purchase_order = erp_client.get_purchase_order(invoice.purchase_order_id)
        except ErpClientError as exc:
            transient = classify_transient_error(exc)
            if transient is not None:
                raise transient from exc
            raise WorkflowError(exc.message) from exc
        purchase_order_exists = purchase_order is not None

    decision = decide_workflow(
        invoice,
        RuleContext(
            supported_currencies=settings.supported_currencies,
            auto_approval_confidence_threshold=(
                settings.auto_approval_confidence_threshold
            ),
            auto_approval_limit=settings.auto_approval_limit,
            totals_tolerance=settings.totals_tolerance,
            supplier_exists=supplier_exists,
            purchase_order_exists=purchase_order_exists,
            is_duplicate=duplicate is not None,
        ),
    )
    session.add(
        DecisionRecord(
            document_id=document.id,
            decision=decision.decision.value,
            reason=decision.reason,
            actor=decision.actor,
        ),
    )
    _audit(
        session,
        document_id=document.id,
        action="validation_completed",
        actor="system",
        details={
            "decision": decision.decision.value,
            "issue_codes": [issue.code for issue in decision.validation.issues],
        },
    )
    if decision.validation.issues:
        obs_metrics.record_validation_failures(
            [issue.code for issue in decision.validation.issues],
        )

    if decision.decision is DecisionType.NEEDS_REVIEW:
        _transition(
            session,
            document,
            WorkflowState.NEEDS_REVIEW,
            actor="system",
            details={"reason_code": "validation_review"},
        )
        obs_metrics.record_human_review_routed()
        _record_outcome(document, "needs_review")
        log_event(
            logger,
            service="worker",
            event="document_needs_review",
            document_id=str(document.id),
            correlation_id=str(document.correlation_id),
            workflow_state=document.status,
            outcome="needs_review",
        )
        session.flush()
        return document

    obs_metrics.record_auto_approval()
    _transition(
        session,
        document,
        WorkflowState.APPROVED,
        actor="system",
    )
    session.flush()
    return _submit_approved_invoice(
        session,
        document=document,
        invoice=invoice,
        erp_client=erp_client,
        provider_name=provider_name,
    )


def process_document(
    session: Session,
    *,
    document: DocumentRecord,
    settings: Settings,
    provider: LLMProvider,
    erp_client: ErpGateway,
) -> DocumentRecord:
    try:
        state = WorkflowState(document.status)

        if state in WORKER_NOOP_STATES:
            return document

        if state == WorkflowState.RECEIVED:
            raise WorkflowError("Document must be queued before worker processing")

        if state == WorkflowState.QUEUED:
            _transition(
                session,
                document,
                WorkflowState.PROCESSING,
                actor="system",
            )
            session.flush()
            state = WorkflowState.PROCESSING

        if state == WorkflowState.APPROVED:
            extraction_record = _latest_extraction(document)
            if extraction_record is None:
                raise WorkflowError("Approved document is missing extraction")
            invoice = Invoice.model_validate(extraction_record.payload_json)
            return _submit_approved_invoice(
                session,
                document=document,
                invoice=invoice,
                erp_client=erp_client,
                provider_name=extraction_record.provider,
            )

        if state == WorkflowState.EXTRACTED:
            extraction_record = _latest_extraction(document)
            if extraction_record is None:
                raise WorkflowError("Extracted document is missing extraction")
            invoice = Invoice.model_validate(extraction_record.payload_json)
            return _decide_and_maybe_submit(
                session,
                document=document,
                invoice=invoice,
                settings=settings,
                erp_client=erp_client,
                provider_name=extraction_record.provider,
            )

        if state != WorkflowState.PROCESSING:
            raise WorkflowError(f"Unexpected document state: {state.value}")

        existing_submission = _submission_for_document(session, document)
        if existing_submission is not None:
            _transition(
                session,
                document,
                WorkflowState.EXTRACTED,
                actor="system",
                details={"idempotent": True},
            )
            session.flush()
            _transition(
                session,
                document,
                WorkflowState.APPROVED,
                actor="system",
                details={"idempotent": True},
            )
            session.flush()
            _transition(
                session,
                document,
                WorkflowState.SUBMITTED,
                actor="system",
                details={"idempotent": True},
            )
            session.flush()
            return document

        extraction_record = _latest_extraction(document)
        if extraction_record is None:
            try:
                document_text = extract_text_from_pdf(Path(document.raw_path))
            except PdfTextExtractionError as exc:
                _transition(
                    session,
                    document,
                    WorkflowState.FAILED,
                    actor="system",
                    details={"error_class": "pdf_extraction_error"},
                )
                _record_outcome(document, "failed")
                log_event(
                    logger,
                    service="worker",
                    event="document_failed",
                    document_id=str(document.id),
                    correlation_id=str(document.correlation_id),
                    workflow_state="failed",
                    error_class="pdf_extraction_error",
                    outcome="failed",
                )
                raise WorkflowError(exc.message) from exc

            try:
                with start_span(
                    "provider.extract",
                    attributes=safe_span_attributes(
                        document_id=str(document.id),
                        correlation_id=str(document.correlation_id),
                        workflow_state=document.status,
                    ),
                ):
                    extraction_result = provider.extract(
                        ExtractionRequest(
                            document_text=document_text,
                            correlation_id=document.correlation_id,
                            document_id=document.id,
                        ),
                    )
            except ProviderOutputError:
                obs_metrics.record_extraction(
                    provider="unknown",
                    model_name=None,
                    latency_ms=None,
                    outcome="failure",
                    error_class="provider_output_error",
                )
                obs_metrics.record_validation_failures(["provider_output_error"])
                _transition(
                    session,
                    document,
                    WorkflowState.NEEDS_REVIEW,
                    actor="system",
                    details={"error_class": "provider_output_error"},
                )
                _audit(
                    session,
                    document_id=document.id,
                    action="provider_output_invalid",
                    actor="system",
                    details={
                        "provider": "unknown",
                        "error_class": "provider_output_error",
                    },
                )
                obs_metrics.record_human_review_routed()
                _record_outcome(document, "needs_review")
                log_event(
                    logger,
                    service="worker",
                    event="document_needs_review",
                    document_id=str(document.id),
                    correlation_id=str(document.correlation_id),
                    workflow_state="needs_review",
                    error_class="provider_output_error",
                    outcome="needs_review",
                )
                session.flush()
                return document
            except NonRecoverableProviderError as exc:
                obs_metrics.record_extraction(
                    provider=exc.provider,
                    model_name=None,
                    latency_ms=None,
                    outcome="failure",
                    error_class=exc.error_class.value,
                )
                _transition(
                    session,
                    document,
                    WorkflowState.FAILED,
                    actor="system",
                    details={"error_class": exc.error_class.value},
                )
                _record_outcome(document, "failed")
                raise WorkflowError(exc.message) from exc
            except Exception as exc:
                transient = classify_transient_error(exc)
                if transient is not None:
                    provider_name = "unknown"
                    if isinstance(exc, Exception) and hasattr(exc, "provider"):
                        provider_name = str(getattr(exc, "provider"))
                    obs_metrics.record_extraction(
                        provider=provider_name,
                        model_name=None,
                        latency_ms=None,
                        outcome="failure",
                        error_class=transient.error_class,
                    )
                    raise transient from exc
                raise

            obs_metrics.record_extraction(
                provider=extraction_result.provider,
                model_name=extraction_result.model_name,
                latency_ms=extraction_result.latency_ms,
                outcome="success",
            )
            session.add(
                ExtractionRecord(
                    document_id=document.id,
                    provider=extraction_result.provider,
                    model_name=extraction_result.model_name,
                    model_version=extraction_result.model_version,
                    prompt_version=extraction_result.prompt_version,
                    payload_json=extraction_result.invoice.model_dump(mode="json"),
                    overall_confidence=extraction_result.invoice.overall_confidence,
                    latency_ms=extraction_result.latency_ms,
                    token_usage=extraction_result.token_usage,
                    fallback_reason=extraction_result.fallback_reason,
                ),
            )
            _audit(
                session,
                document_id=document.id,
                action="extraction_completed",
                actor="system",
                details={
                    "provider": extraction_result.provider,
                    "model_name": extraction_result.model_name,
                    "prompt_version": extraction_result.prompt_version,
                    "fallback_reason": extraction_result.fallback_reason,
                },
            )
            _transition(
                session,
                document,
                WorkflowState.EXTRACTED,
                actor="system",
            )
            session.flush()
            invoice = extraction_result.invoice
            provider_name = extraction_result.provider
        else:
            invoice = Invoice.model_validate(extraction_record.payload_json)
            provider_name = extraction_record.provider
            if WorkflowState(document.status) == WorkflowState.PROCESSING:
                _transition(
                    session,
                    document,
                    WorkflowState.EXTRACTED,
                    actor="system",
                    details={"idempotent": True},
                )
                session.flush()

        return _decide_and_maybe_submit(
            session,
            document=document,
            invoice=invoice,
            settings=settings,
            erp_client=erp_client,
            provider_name=provider_name,
        )
    except TransientWorkerError:
        raise
    except IllegalStateTransitionError as exc:
        _audit(
            session,
            document_id=document.id,
            action="illegal_transition",
            actor="system",
            details={
                "from_state": exc.current_state.value,
                "to_state": exc.target_state.value,
            },
        )
        raise
    except WorkflowError:
        raise
    except Exception as exc:
        transient = classify_transient_error(exc)
        if transient is not None:
            raise transient from exc
        if WorkflowState(document.status) not in TERMINAL_STATES:
            try:
                _transition(
                    session,
                    document,
                    WorkflowState.FAILED,
                    actor="system",
                    details={"error_class": "unexpected_error"},
                )
            except IllegalStateTransitionError:
                pass
        logger.info(
            "document_failed correlation_id=%s document_id=%s error_class=%s",
            document.correlation_id,
            document.id,
            type(exc).__name__,
        )
        raise WorkflowError("Document processing failed") from exc


def create_received_document(
    session: Session,
    *,
    idempotency_key: str,
    source_message_id: str,
    content_hash: str,
    raw_path: str,
    correlation_id: UUID | None = None,
) -> DocumentRecord:
    document = DocumentRecord(
        id=uuid4(),
        idempotency_key=idempotency_key,
        source_message_id=source_message_id,
        content_hash=content_hash,
        raw_path=raw_path,
        status=WorkflowState.RECEIVED.value,
        correlation_id=correlation_id or uuid4(),
        retry_count=0,
        last_error_class=None,
    )
    session.add(document)
    _audit(
        session,
        document_id=document.id,
        action="document_received",
        actor="system",
        details={"source_message_id_present": True},
    )
    session.flush()
    return document
