import logging
import os
from uuid import UUID

from celery import Task  # type: ignore[import-untyped]
from celery.signals import worker_ready  # type: ignore[import-untyped]
from sqlalchemy import select

from services.observability import metrics as obs_metrics
from services.observability.logging import configure_structured_logging, log_event
from services.observability.tracing import (
    instrument_celery,
    instrument_httpx,
    safe_span_attributes,
    setup_tracing,
    start_span,
)
from services.persistence.db import (
    create_db_engine,
    create_session_factory,
    session_scope,
)
from services.persistence.models import DocumentRecord
from services.settings import load_settings
from services.worker.celery_app import celery_app
from services.worker.dlq import DeadLetterQueue
from services.worker.executor.erp_client import ErpClient
from services.worker.providers.contract import LLMProvider
from services.worker.providers.router import build_llm_provider
from services.worker.transient import TransientWorkerError
from services.worker.workflow.process_document import (
    WorkflowError,
    mark_retry_exhausted,
    process_document,
    record_retry_metadata,
)

logger = logging.getLogger(__name__)
_METRICS_SERVER_STARTED = False


def _start_worker_observability() -> None:
    global _METRICS_SERVER_STARTED
    configure_structured_logging(service="worker")
    setup_tracing(service_name="docflow-worker")
    instrument_celery()
    instrument_httpx()
    try:
        settings = load_settings()
        obs_metrics.configure_dlq_collector(settings.redis_url)
    except Exception:
        logger.debug("worker_dlq_refresh_failed", exc_info=False)
    if _METRICS_SERVER_STARTED:
        return
    try:
        from prometheus_client import start_http_server

        port = int(os.environ.get("WORKER_METRICS_PORT", "9101"))
        start_http_server(port, registry=obs_metrics.registry())
        _METRICS_SERVER_STARTED = True
    except Exception:
        logger.debug("worker_metrics_server_failed", exc_info=False)


@worker_ready.connect  # type: ignore[untyped-decorator]
def _on_worker_ready(**_: object) -> None:
    _start_worker_observability()


def run_document_processing(
    *,
    document_id: UUID,
    correlation_id: UUID,
    provider: LLMProvider | None = None,
    erp_client: ErpClient | None = None,
) -> str:
    settings = load_settings()
    engine = create_db_engine(settings)
    factory = create_session_factory(engine)
    resolved_provider = provider or build_llm_provider(settings)
    owns_erp = erp_client is None
    resolved_erp = erp_client or ErpClient(
        base_url=settings.erp_base_url,
        timeout_seconds=settings.erp_timeout_seconds,
    )
    try:
        with session_scope(factory) as session:
            document = session.execute(
                select(DocumentRecord)
                .where(DocumentRecord.id == document_id)
                .with_for_update(),
            ).scalar_one_or_none()
            if document is None:
                raise WorkflowError("Document not found")
            if document.correlation_id != correlation_id:
                raise WorkflowError("Correlation id mismatch")
            with start_span(
                "worker.process_document",
                attributes=safe_span_attributes(
                    document_id=str(document_id),
                    correlation_id=str(correlation_id),
                    workflow_state=document.status,
                ),
            ):
                process_document(
                    session,
                    document=document,
                    settings=settings,
                    provider=resolved_provider,
                    erp_client=resolved_erp,
                )
            return document.status
    finally:
        if owns_erp:
            resolved_erp.close()
        engine.dispose()


def finalize_exhausted_failure(
    *,
    document_id: UUID,
    correlation_id: UUID,
    retry_count: int,
    error_class: str,
    error_message: str,
) -> None:
    settings = load_settings()
    engine = create_db_engine(settings)
    factory = create_session_factory(engine)
    dlq = DeadLetterQueue(redis_url=settings.redis_url)
    try:
        with session_scope(factory) as session:
            document = session.execute(
                select(DocumentRecord)
                .where(DocumentRecord.id == document_id)
                .with_for_update(),
            ).scalar_one_or_none()
            if document is None:
                return
            mark_retry_exhausted(
                session,
                document,
                retry_count=retry_count,
                error_class=error_class,
            )
        dlq.push(
            document_id=document_id,
            correlation_id=correlation_id,
            retry_count=retry_count,
            error_class=error_class,
            error_message=error_message,
        )
        obs_metrics.configure_dlq_collector(settings.redis_url)
        log_event(
            logger,
            service="worker",
            event="document_dlq",
            document_id=str(document_id),
            correlation_id=str(correlation_id),
            retry_count=retry_count,
            error_class=error_class,
            outcome="dlq",
        )
    finally:
        dlq.close()
        engine.dispose()


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="services.worker.tasks.process_document_task",
    max_retries=3,
    acks_late=True,
)
def process_document_task(
    self: Task,
    document_id: str,
    correlation_id: str,
) -> dict[str, str]:
    _start_worker_observability()
    settings = load_settings()
    doc_uuid = UUID(document_id)
    corr_uuid = UUID(correlation_id)
    max_retries = settings.celery_worker_max_retries
    try:
        status = run_document_processing(
            document_id=doc_uuid,
            correlation_id=corr_uuid,
        )
        return {"status": status}
    except TransientWorkerError as exc:
        retries_so_far = int(self.request.retries)
        next_retry_count = retries_so_far + 1
        engine = create_db_engine(settings)
        factory = create_session_factory(engine)
        try:
            with session_scope(factory) as session:
                document = session.get(DocumentRecord, doc_uuid)
                if document is not None:
                    record_retry_metadata(
                        session,
                        document,
                        retry_count=next_retry_count,
                        error_class=exc.error_class,
                    )
        finally:
            engine.dispose()
        operation = (
            "erp_submit" if exc.error_class == "erp_transient" else "process_document"
        )
        obs_metrics.record_retry(
            operation=operation,
            error_class=exc.error_class,
        )
        log_event(
            logger,
            service="worker",
            event="worker_transient_failure",
            document_id=document_id,
            correlation_id=correlation_id,
            retry_count=next_retry_count,
            error_class=exc.error_class,
            outcome="retry",
        )
        if retries_so_far >= max_retries:
            finalize_exhausted_failure(
                document_id=doc_uuid,
                correlation_id=corr_uuid,
                retry_count=next_retry_count,
                error_class=exc.error_class,
                error_message=exc.message,
            )
            return {"status": "failed"}
        countdown = min(
            settings.celery_retry_backoff_base**retries_so_far,
            settings.celery_retry_backoff_max,
        )
        raise self.retry(
            exc=exc,
            countdown=countdown,
            max_retries=max_retries,
        ) from exc
    except WorkflowError:
        log_event(
            logger,
            service="worker",
            event="worker_workflow_error",
            document_id=document_id,
            correlation_id=correlation_id,
            outcome="failed",
        )
        engine = create_db_engine(settings)
        factory = create_session_factory(engine)
        try:
            with session_scope(factory) as session:
                document = session.get(DocumentRecord, doc_uuid)
                status_value = document.status if document is not None else "failed"
        finally:
            engine.dispose()
        return {"status": status_value}
