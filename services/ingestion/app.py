import logging
from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from services.ingestion.api_schemas import (
    AuditEventSummary,
    DecisionSummary,
    DlqEntryResponse,
    DlqListResponse,
    DocumentAcceptedResponse,
    DocumentStatusResponse,
    ExtractionSummary,
    FailureSummary,
    HealthResponse,
    ReviewActionRequest,
    ReviewActionResponse,
    ReviewDetailResponse,
    ReviewListItem,
    ReviewListResponse,
    ValidationIssueSummary,
)
from services.ingestion.idempotency import build_idempotency_key, content_sha256
from services.ingestion.storage import store_pdf
from services.observability import metrics as obs_metrics
from services.observability.logging import configure_structured_logging, log_event
from services.observability.tracing import (
    instrument_celery,
    instrument_fastapi,
    instrument_httpx,
    setup_tracing,
    start_span,
)
from services.persistence.db import (
    create_db_engine,
    create_session_factory,
    session_scope,
)
from services.persistence.models import DocumentRecord
from services.settings import Settings, load_settings
from services.worker.celery_app import enqueue_process_document
from services.worker.dlq import DeadLetterQueue
from services.worker.executor.erp_client import ErpClient, ErpGateway
from services.worker.workflow.process_document import (
    create_received_document,
    queue_document,
)
from services.worker.workflow.review import (
    ReviewConflictError,
    ReviewError,
    ReviewNotFoundError,
    ReviewValidationError,
    approve_review,
    get_review_document,
    list_needs_review_documents,
    reject_review,
    resolve_source_path,
    review_detail_payload,
)

logger = logging.getLogger(__name__)

EnqueueFn = Callable[[UUID, UUID], None]


def create_app(
    settings: Settings | None = None,
    *,
    session_factory: sessionmaker[Session] | None = None,
    enqueue_document: EnqueueFn | None = None,
    erp_client: ErpGateway | None = None,
) -> FastAPI:
    resolved_settings = settings or load_settings()
    configure_structured_logging(service="ingestion")
    setup_tracing(service_name="docflow-ingestion")
    instrument_celery()
    instrument_httpx()
    obs_metrics.configure_dlq_collector(resolved_settings.redis_url)
    engine = create_db_engine(resolved_settings)
    resolved_session_factory = session_factory or create_session_factory(engine)
    resolved_erp: ErpGateway = erp_client or ErpClient(
        base_url=resolved_settings.erp_base_url,
        timeout_seconds=resolved_settings.erp_timeout_seconds,
    )

    def default_enqueue(document_id: UUID, correlation_id: UUID) -> None:
        enqueue_process_document(document_id, correlation_id)

    resolved_enqueue = enqueue_document or default_enqueue
    app = FastAPI(title="DocFlow AI Ingestion")
    app.state.settings = resolved_settings
    app.state.session_factory = resolved_session_factory
    app.state.enqueue_document = resolved_enqueue
    app.state.erp_client = resolved_erp
    instrument_fastapi(app)

    def get_settings() -> Settings:
        return resolved_settings

    def get_session_factory() -> sessionmaker[Session]:
        return resolved_session_factory

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(
            content=obs_metrics.metrics_payload(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.get("/dlq", response_model=DlqListResponse)
    def list_dlq(
        settings_dep: Annotated[Settings, Depends(get_settings)],
    ) -> DlqListResponse:
        dlq = DeadLetterQueue(redis_url=settings_dep.redis_url)
        try:
            entries = [
                DlqEntryResponse(
                    document_id=str(item.get("document_id", "")),
                    correlation_id=str(item.get("correlation_id", "")),
                    retry_count=int(item.get("retry_count", 0)),
                    error_class=str(item.get("error_class", "")),
                    error_message=str(item.get("error_message", "")),
                    failed_at=str(item.get("failed_at", "")),
                )
                for item in dlq.list_entries(limit=100)
            ]
            return DlqListResponse(depth=dlq.depth(), entries=entries)
        finally:
            dlq.close()

    @app.post(
        "/documents",
        response_model=DocumentAcceptedResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def upload_document(
        source_message_id: Annotated[str, Form(min_length=1)],
        file: Annotated[UploadFile, File()],
        settings_dep: Annotated[Settings, Depends(get_settings)],
        factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    ) -> DocumentAcceptedResponse:
        if file.content_type not in {"application/pdf", "application/x-pdf"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only PDF uploads are supported",
            )
        content = file.file.read()
        if not content:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded PDF is empty",
            )
        if len(content) > settings_dep.max_upload_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded PDF exceeds size limit",
            )
        content_hash = content_sha256(content)
        idempotency_key = build_idempotency_key(
            source_message_id=source_message_id,
            content_hash=content_hash,
        )
        with session_scope(factory) as session:
            existing = session.scalar(
                select(DocumentRecord).where(
                    DocumentRecord.idempotency_key == idempotency_key,
                ),
            )
            if existing is not None:
                log_event(
                    logger,
                    service="ingestion",
                    event="idempotent_replay",
                    document_id=str(existing.id),
                    correlation_id=str(existing.correlation_id),
                    workflow_state=existing.status,
                    outcome="replay",
                )
                return DocumentAcceptedResponse(
                    document_id=existing.id,
                    correlation_id=existing.correlation_id,
                    status=existing.status,
                    idempotent_replay=True,
                )
            with start_span(
                "ingestion.accept_document",
                attributes={"operation": "upload"},
            ):
                document = create_received_document(
                    session,
                    idempotency_key=idempotency_key,
                    source_message_id=source_message_id,
                    content_hash=content_hash,
                    raw_path="",
                )
                raw_path = store_pdf(
                    storage_dir=settings_dep.document_storage_dir,
                    document_id=document.id,
                    content=content,
                )
                document.raw_path = str(raw_path)
                queue_document(session, document)
                session.flush()
                document_id = document.id
                correlation_id = document.correlation_id
                queued_status = document.status

        try:
            resolved_enqueue(document_id, correlation_id)
        except Exception:
            log_event(
                logger,
                service="ingestion",
                event="enqueue_failed",
                document_id=str(document_id),
                correlation_id=str(correlation_id),
                outcome="failed",
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed to enqueue document processing",
            ) from None

        obs_metrics.record_document_ingested(service="ingestion")
        log_event(
            logger,
            service="ingestion",
            event="document_queued",
            document_id=str(document_id),
            correlation_id=str(correlation_id),
            workflow_state=queued_status,
            outcome="queued",
        )
        return DocumentAcceptedResponse(
            document_id=document_id,
            correlation_id=correlation_id,
            status=queued_status,
            idempotent_replay=False,
        )

    @app.get("/documents/{document_id}", response_model=DocumentStatusResponse)
    def get_document(
        document_id: UUID,
        settings_dep: Annotated[Settings, Depends(get_settings)],
        factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    ) -> DocumentStatusResponse:
        with session_scope(factory) as session:
            document = session.get(DocumentRecord, document_id)
            if document is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Document not found",
                )
            decision = None
            if document.decisions:
                latest = max(document.decisions, key=lambda item: item.created_at)
                decision = DecisionSummary(
                    decision=latest.decision,
                    reason=latest.reason,
                    actor=latest.actor,
                    created_at=latest.created_at,
                )
            audit_summary = [
                AuditEventSummary(
                    action=event.action,
                    actor=event.actor,
                    created_at=event.created_at,
                    details=event.details_json,
                )
                for event in sorted(
                    document.audit_events,
                    key=lambda item: item.created_at,
                )
            ]
            failure = None
            dlq_present = False
            if document.status == "failed" or document.last_error_class is not None:
                dlq = DeadLetterQueue(redis_url=settings_dep.redis_url)
                try:
                    dlq_entry = dlq.find_for_document(document.id)
                    dlq_present = dlq_entry is not None
                finally:
                    dlq.close()
                if document.last_error_class is not None:
                    failure = FailureSummary(
                        error_class=document.last_error_class,
                        retry_count=document.retry_count,
                        dlq_present=dlq_present,
                    )
            return DocumentStatusResponse(
                document_id=document.id,
                correlation_id=document.correlation_id,
                status=document.status,
                source_message_id=document.source_message_id,
                content_hash=document.content_hash,
                created_at=document.created_at,
                updated_at=document.updated_at,
                retry_count=document.retry_count,
                last_error_class=document.last_error_class,
                failure=failure,
                decision=decision,
                audit_summary=audit_summary,
            )

    @app.get(
        "/documents/{document_id}/source",
        response_class=FileResponse,
    )
    def get_document_source(
        document_id: UUID,
        factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    ) -> FileResponse:
        with session_scope(factory) as session:
            try:
                document = get_review_document(session, document_id)
                path = resolve_source_path(document)
            except ReviewNotFoundError as exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=exc.message,
                ) from exc
            return FileResponse(
                path=path,
                media_type="application/pdf",
                filename=f"{document_id}.pdf",
            )

    @app.get("/reviews", response_model=ReviewListResponse)
    def list_reviews(
        factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    ) -> ReviewListResponse:
        with session_scope(factory) as session:
            documents = list_needs_review_documents(session)
            items: list[ReviewListItem] = []
            for document in documents:
                extraction = None
                if document.extractions:
                    extraction = max(
                        document.extractions,
                        key=lambda item: item.created_at,
                    )
                decision = None
                if document.decisions:
                    decision = max(
                        document.decisions,
                        key=lambda item: item.created_at,
                    )
                items.append(
                    ReviewListItem(
                        document_id=document.id,
                        correlation_id=document.correlation_id,
                        status=document.status,
                        source_message_id=document.source_message_id,
                        created_at=document.created_at,
                        updated_at=document.updated_at,
                        overall_confidence=(
                            extraction.overall_confidence
                            if extraction is not None
                            else None
                        ),
                        decision_reason=(
                            decision.reason if decision is not None else None
                        ),
                    ),
                )
            return ReviewListResponse(items=items)

    @app.get("/reviews/{document_id}", response_model=ReviewDetailResponse)
    def get_review(
        document_id: UUID,
        factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    ) -> ReviewDetailResponse:
        with session_scope(factory) as session:
            try:
                document = get_review_document(session, document_id)
            except ReviewNotFoundError as exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=exc.message,
                ) from exc
            payload = review_detail_payload(document)
            extraction = payload["extraction"]
            decision = payload["decision"]
            return ReviewDetailResponse(
                document_id=payload["document_id"],
                correlation_id=payload["correlation_id"],
                status=payload["status"],
                source_message_id=payload["source_message_id"],
                content_hash=payload["content_hash"],
                created_at=payload["created_at"],
                updated_at=payload["updated_at"],
                extraction=(
                    ExtractionSummary.model_validate(extraction)
                    if isinstance(extraction, dict)
                    else None
                ),
                decision=(
                    DecisionSummary.model_validate(decision)
                    if isinstance(decision, dict)
                    else None
                ),
                validation_issues=[
                    ValidationIssueSummary.model_validate(issue)
                    for issue in payload["validation_issues"]
                ],
                audit_summary=[
                    AuditEventSummary.model_validate(event)
                    for event in payload["audit_summary"]
                ],
                source_available=bool(payload["source_available"]),
            )

    @app.post(
        "/reviews/{document_id}/approve",
        response_model=ReviewActionResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def approve_document_review(
        document_id: UUID,
        body: ReviewActionRequest,
        settings_dep: Annotated[Settings, Depends(get_settings)],
        factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    ) -> ReviewActionResponse:
        try:
            with session_scope(factory) as session:
                document, changed = approve_review(
                    session,
                    document_id=document_id,
                    actor=body.actor.strip(),
                    reason=body.reason.strip(),
                    settings=settings_dep,
                    erp_client=resolved_erp,
                    edited_invoice=body.edited_invoice,
                )
                correlation_id = document.correlation_id
                status_value = document.status
                should_enqueue = changed and status_value == "approved"
        except ReviewNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=exc.message,
            ) from exc
        except ReviewValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "message": exc.message,
                    "issues": [
                        {"code": issue.code, "message": issue.message}
                        for issue in exc.issues
                    ],
                },
            ) from exc
        except ReviewConflictError as exc:
            obs_metrics.record_review_action(action="approve", outcome="conflict")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": exc.message, "status": exc.status},
            ) from exc
        except ReviewError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=exc.message,
            ) from exc

        resumed = False
        if should_enqueue:
            try:
                resolved_enqueue(document_id, correlation_id)
                resumed = True
            except Exception:
                log_event(
                    logger,
                    service="ingestion",
                    event="review_resume_enqueue_failed",
                    document_id=str(document_id),
                    correlation_id=str(correlation_id),
                    outcome="failed",
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Failed to enqueue review resume",
                ) from None
            with session_scope(factory) as session:
                refreshed = session.get(DocumentRecord, document_id)
                if refreshed is not None:
                    status_value = refreshed.status

        obs_metrics.record_review_action(
            action="approve",
            outcome="unchanged" if not changed else "accepted",
        )
        return ReviewActionResponse(
            document_id=document_id,
            correlation_id=correlation_id,
            status=status_value,
            resumed=resumed,
            idempotent_replay=not changed,
        )

    @app.post(
        "/reviews/{document_id}/reject",
        response_model=ReviewActionResponse,
    )
    def reject_document_review(
        document_id: UUID,
        body: ReviewActionRequest,
        factory: Annotated[sessionmaker[Session], Depends(get_session_factory)],
    ) -> ReviewActionResponse:
        if body.edited_invoice is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reject does not accept edited_invoice",
            )
        try:
            with session_scope(factory) as session:
                document, changed = reject_review(
                    session,
                    document_id=document_id,
                    actor=body.actor.strip(),
                    reason=body.reason.strip(),
                )
                obs_metrics.record_review_action(
                    action="reject",
                    outcome="unchanged" if not changed else "rejected",
                )
                if changed:
                    obs_metrics.record_workflow_outcome(
                        final_state="rejected",
                        duration_seconds=None,
                    )
                return ReviewActionResponse(
                    document_id=document.id,
                    correlation_id=document.correlation_id,
                    status=document.status,
                    resumed=False,
                    idempotent_replay=not changed,
                )
        except ReviewNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=exc.message,
            ) from exc
        except ReviewConflictError as exc:
            obs_metrics.record_review_action(action="reject", outcome="conflict")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": exc.message, "status": exc.status},
            ) from exc

    return app


def get_app() -> FastAPI:
    return create_app()
