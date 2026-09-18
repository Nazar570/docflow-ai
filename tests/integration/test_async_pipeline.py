from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from services.ingestion.app import create_app
from services.persistence.db import session_scope
from services.persistence.models import DocumentRecord, SubmittedInvoiceRecord
from services.settings import Settings
from services.worker.dlq import DeadLetterQueue
from services.worker.executor.erp_client import (
    ErpClient,
    ErpClientError,
    ErpInvoice,
    ErpInvoiceCreateRequest,
    ErpPurchaseOrder,
    ErpSupplier,
)
from services.worker.extraction.pdf_samples import build_pdf_bytes
from services.worker.providers.contract import ExtractionRequest, ExtractionResult
from services.worker.providers.errors import (
    ProviderErrorClass,
    RecoverableProviderError,
)
from services.worker.providers.fake import FakeLLMProvider
from services.worker.transient import TransientWorkerError
from services.worker.workflow.process_document import (
    mark_retry_exhausted,
    process_document,
    record_retry_metadata,
)

pytestmark = pytest.mark.integration


class FlakyProvider:
    def __init__(self, inner: FakeLLMProvider, *, fail_times: int) -> None:
        self._inner = inner
        self._fail_times = fail_times
        self.calls = 0

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise RecoverableProviderError(
                "simulated provider timeout",
                error_class=ProviderErrorClass.TIMEOUT,
                provider="fake",
            )
        return self._inner.extract(request)


class FlakyErpClient:
    def __init__(self, inner: ErpClient, *, fail_times: int) -> None:
        self._inner = inner
        self._fail_times = fail_times
        self.create_calls = 0

    def close(self) -> None:
        self._inner.close()

    def get_supplier(self, supplier_id: str) -> ErpSupplier | None:
        return self._inner.get_supplier(supplier_id)

    def get_purchase_order(self, purchase_order_id: str) -> ErpPurchaseOrder | None:
        return self._inner.get_purchase_order(purchase_order_id)

    def create_invoice(
        self,
        payload: ErpInvoiceCreateRequest,
        *,
        idempotency_key: str,
    ) -> ErpInvoice:
        self.create_calls += 1
        if self.create_calls <= self._fail_times:
            raise ErpClientError("simulated erp outage", status_code=503)
        return self._inner.create_invoice(payload, idempotency_key=idempotency_key)


def test_post_documents_returns_202_without_waiting_for_processing(
    ingestion_client_deferred: tuple[TestClient, list[tuple[UUID, UUID]]],
) -> None:
    client, queued = ingestion_client_deferred
    pdf_bytes = build_pdf_bytes(
        "Fictional invoice for Acme Supplies GmbH INV-1001 EUR 125.50 async",
    )
    response = client.post(
        "/documents",
        data={"source_message_id": "msg-async-202"},
        files={"file": ("invoice.pdf", pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["idempotent_replay"] is False
    assert len(queued) == 1
    status_body = client.get(f"/documents/{body['document_id']}").json()
    assert status_body["status"] == "queued"


def test_worker_completes_fake_provider_document_to_submitted(
    ingestion_client: TestClient,
) -> None:
    pdf_bytes = build_pdf_bytes(
        "Fictional invoice for Acme Supplies GmbH INV-1001 EUR 125.50 worker",
    )
    response = ingestion_client.post(
        "/documents",
        data={"source_message_id": "msg-worker-success"},
        files={"file": ("invoice.pdf", pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 202
    document_id = response.json()["document_id"]
    status_body = ingestion_client.get(f"/documents/{document_id}").json()
    assert status_body["status"] == "submitted"
    assert status_body["decision"]["decision"] == "auto_approve"
    assert status_body["retry_count"] == 0


def test_worker_completes_low_confidence_to_needs_review(
    ingestion_client: TestClient,
    erp_client: ErpClient,
) -> None:
    before = erp_client._client.get("/_debug/invoice_count").json()["count"]
    pdf_bytes = build_pdf_bytes(
        "DOC FLOW SCENARIO:LOW_CONFIDENCE fictional low confidence invoice async",
    )
    response = ingestion_client.post(
        "/documents",
        data={"source_message_id": "msg-worker-review"},
        files={"file": ("invoice.pdf", pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 202
    document_id = response.json()["document_id"]
    status_body = ingestion_client.get(f"/documents/{document_id}").json()
    assert status_body["status"] == "needs_review"
    after = erp_client._client.get("/_debug/invoice_count").json()["count"]
    assert after == before


def test_transient_failure_retries_then_succeeds_without_duplicate_erp(
    settings: Settings,
    migrated_db: sessionmaker[Session],
    erp_client: ErpClient,
    fake_provider: FakeLLMProvider,
) -> None:
    flaky = FlakyProvider(fake_provider, fail_times=2)
    before = erp_client._client.get("/_debug/invoice_count").json()["count"]

    def enqueue(document_id: UUID, correlation_id: UUID) -> None:
        attempts = 0
        while True:
            try:
                with session_scope(migrated_db) as session:
                    document = session.get(DocumentRecord, document_id)
                    assert document is not None
                    process_document(
                        session,
                        document=document,
                        settings=settings,
                        provider=flaky,
                        erp_client=erp_client,
                    )
                return
            except TransientWorkerError as exc:
                attempts += 1
                with session_scope(migrated_db) as session:
                    document = session.get(DocumentRecord, document_id)
                    assert document is not None
                    record_retry_metadata(
                        session,
                        document,
                        retry_count=attempts,
                        error_class=exc.error_class,
                    )
                if attempts > settings.celery_worker_max_retries:
                    raise

    app = create_app(
        settings,
        session_factory=migrated_db,
        enqueue_document=enqueue,
    )
    with TestClient(app) as client:
        pdf_bytes = build_pdf_bytes(
            "Fictional invoice for Acme Supplies GmbH INV-1001 EUR 125.50 retry-ok",
        )
        response = client.post(
            "/documents",
            data={"source_message_id": "msg-retry-ok"},
            files={"file": ("invoice.pdf", pdf_bytes, "application/pdf")},
        )
        assert response.status_code == 202
        document_id = response.json()["document_id"]
        status_body = client.get(f"/documents/{document_id}").json()
        assert status_body["status"] == "submitted"
        assert status_body["retry_count"] == 2
        assert flaky.calls == 3
    after = erp_client._client.get("/_debug/invoice_count").json()["count"]
    assert after == before + 1
    with session_scope(migrated_db) as session:
        rows = session.scalars(
            select(SubmittedInvoiceRecord).where(
                SubmittedInvoiceRecord.document_id == UUID(document_id),
            ),
        ).all()
        assert len(rows) == 1


def test_retry_exhaustion_writes_dlq_and_failed_state(
    settings: Settings,
    migrated_db: sessionmaker[Session],
    erp_client: ErpClient,
    fake_provider: FakeLLMProvider,
    cleared_dlq: DeadLetterQueue,
) -> None:
    settings = settings.model_copy(update={"celery_worker_max_retries": 2})
    flaky = FlakyProvider(fake_provider, fail_times=100)

    def enqueue(document_id: UUID, correlation_id: UUID) -> None:
        attempts = 0
        while True:
            try:
                with session_scope(migrated_db) as session:
                    document = session.get(DocumentRecord, document_id)
                    assert document is not None
                    process_document(
                        session,
                        document=document,
                        settings=settings,
                        provider=flaky,
                        erp_client=erp_client,
                    )
                return
            except TransientWorkerError as exc:
                attempts += 1
                with session_scope(migrated_db) as session:
                    document = session.get(DocumentRecord, document_id)
                    assert document is not None
                    record_retry_metadata(
                        session,
                        document,
                        retry_count=attempts,
                        error_class=exc.error_class,
                    )
                if attempts > settings.celery_worker_max_retries:
                    with session_scope(migrated_db) as session:
                        document = session.get(DocumentRecord, document_id)
                        assert document is not None
                        mark_retry_exhausted(
                            session,
                            document,
                            retry_count=attempts,
                            error_class=exc.error_class,
                        )
                    cleared_dlq.push(
                        document_id=document_id,
                        correlation_id=correlation_id,
                        retry_count=attempts,
                        error_class=exc.error_class,
                        error_message=exc.message,
                    )
                    return

    app = create_app(
        settings,
        session_factory=migrated_db,
        enqueue_document=enqueue,
    )
    with TestClient(app) as client:
        pdf_bytes = build_pdf_bytes(
            "Fictional invoice for Acme Supplies GmbH INV-1001 EUR 125.50 dlq",
        )
        response = client.post(
            "/documents",
            data={"source_message_id": "msg-dlq"},
            files={"file": ("invoice.pdf", pdf_bytes, "application/pdf")},
        )
        assert response.status_code == 202
        document_id = response.json()["document_id"]
        status_body = client.get(f"/documents/{document_id}").json()
        assert status_body["status"] == "failed"
        assert status_body["failure"] is not None
        assert status_body["failure"]["dlq_present"] is True
        assert status_body["failure"]["error_class"] == "timeout"
        dlq_response = client.get("/dlq")
        assert dlq_response.status_code == 200
        dlq_body = dlq_response.json()
        assert dlq_body["depth"] >= 1
        assert any(entry["document_id"] == document_id for entry in dlq_body["entries"])


def test_duplicate_task_delivery_does_not_create_second_erp_invoice(
    settings: Settings,
    migrated_db: sessionmaker[Session],
    erp_client: ErpClient,
    fake_provider: FakeLLMProvider,
) -> None:
    processed: list[UUID] = []

    def enqueue(document_id: UUID, correlation_id: UUID) -> None:
        processed.append(document_id)
        with session_scope(migrated_db) as session:
            document = session.get(DocumentRecord, document_id)
            assert document is not None
            process_document(
                session,
                document=document,
                settings=settings,
                provider=fake_provider,
                erp_client=erp_client,
            )
        with session_scope(migrated_db) as session:
            document = session.get(DocumentRecord, document_id)
            assert document is not None
            process_document(
                session,
                document=document,
                settings=settings,
                provider=fake_provider,
                erp_client=erp_client,
            )

    before = erp_client._client.get("/_debug/invoice_count").json()["count"]
    app = create_app(
        settings,
        session_factory=migrated_db,
        enqueue_document=enqueue,
    )
    with TestClient(app) as client:
        pdf_bytes = build_pdf_bytes(
            "Fictional invoice for Acme Supplies GmbH INV-1001 EUR 125.50 dup-task",
        )
        response = client.post(
            "/documents",
            data={"source_message_id": "msg-dup-task"},
            files={"file": ("invoice.pdf", pdf_bytes, "application/pdf")},
        )
        assert response.status_code == 202
        document_id = response.json()["document_id"]
        status_body = client.get(f"/documents/{document_id}").json()
        assert status_body["status"] == "submitted"
        second = client.post(
            "/documents",
            data={"source_message_id": "msg-dup-task"},
            files={"file": ("invoice.pdf", pdf_bytes, "application/pdf")},
        )
        assert second.status_code == 202
        assert second.json()["idempotent_replay"] is True
    after = erp_client._client.get("/_debug/invoice_count").json()["count"]
    assert after == before + 1
    with session_scope(migrated_db) as session:
        rows = session.scalars(
            select(SubmittedInvoiceRecord).where(
                SubmittedInvoiceRecord.document_id == UUID(document_id),
            ),
        ).all()
        assert len(rows) == 1


def test_transient_erp_failure_retries_without_duplicate_submission(
    settings: Settings,
    migrated_db: sessionmaker[Session],
    erp_client: ErpClient,
    fake_provider: FakeLLMProvider,
) -> None:
    flaky_erp = FlakyErpClient(erp_client, fail_times=1)

    def enqueue(document_id: UUID, correlation_id: UUID) -> None:
        attempts = 0
        while True:
            try:
                with session_scope(migrated_db) as session:
                    document = session.get(DocumentRecord, document_id)
                    assert document is not None
                    process_document(
                        session,
                        document=document,
                        settings=settings,
                        provider=fake_provider,
                        erp_client=flaky_erp,
                    )
                return
            except TransientWorkerError as exc:
                attempts += 1
                with session_scope(migrated_db) as session:
                    document = session.get(DocumentRecord, document_id)
                    assert document is not None
                    record_retry_metadata(
                        session,
                        document,
                        retry_count=attempts,
                        error_class=exc.error_class,
                    )
                if attempts > settings.celery_worker_max_retries:
                    raise

    before = erp_client._client.get("/_debug/invoice_count").json()["count"]
    app = create_app(
        settings,
        session_factory=migrated_db,
        enqueue_document=enqueue,
    )
    with TestClient(app) as client:
        pdf_bytes = build_pdf_bytes(
            "Fictional invoice for Acme Supplies GmbH INV-1001 EUR 125.50 erp-retry",
        )
        response = client.post(
            "/documents",
            data={"source_message_id": "msg-erp-retry"},
            files={"file": ("invoice.pdf", pdf_bytes, "application/pdf")},
        )
        assert response.status_code == 202
        document_id = response.json()["document_id"]
        status_body = client.get(f"/documents/{document_id}").json()
        assert status_body["status"] == "submitted"
        assert flaky_erp.create_calls == 2
    after = erp_client._client.get("/_debug/invoice_count").json()["count"]
    assert after == before + 1
