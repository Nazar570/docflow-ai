from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from services.ingestion.app import create_app
from services.persistence.db import session_scope
from services.persistence.models import (
    AuditLogRecord,
    DocumentRecord,
    SubmittedInvoiceRecord,
)
from services.settings import Settings
from services.worker.executor.erp_client import ErpClient
from services.worker.extraction.pdf_samples import build_pdf_bytes
from services.worker.extraction.schemas import Invoice, InvoiceLineItem
from services.worker.providers.fake import FakeLLMProvider
from services.worker.workflow.process_document import process_document
from services.worker.workflow.review import (
    ReviewConflictError,
    approve_review,
    reject_review,
)

pytestmark = pytest.mark.integration


def _valid_invoice(**overrides: object) -> Invoice:
    payload: dict[str, object] = {
        "supplier_name": "Acme Supplies GmbH",
        "supplier_id": "sup-acme-001",
        "purchase_order_id": "po-1001",
        "invoice_number": "INV-1001",
        "invoice_date": date(2026, 3, 15),
        "currency": "EUR",
        "total_amount": Decimal("125.50"),
        "line_items": [
            InvoiceLineItem(
                description="Paper reams",
                quantity=Decimal("5"),
                unit_price=Decimal("10.00"),
                line_total=Decimal("50.00"),
            ),
            InvoiceLineItem(
                description="Toner cartridge",
                quantity=Decimal("1"),
                unit_price=Decimal("75.50"),
                line_total=Decimal("75.50"),
            ),
        ],
        "overall_confidence": 0.96,
    }
    payload.update(overrides)
    return Invoice.model_validate(payload)


def _upload_needs_review(client: TestClient, source_message_id: str) -> str:
    pdf_bytes = build_pdf_bytes(
        "DOC FLOW SCENARIO:LOW_CONFIDENCE fictional low confidence invoice "
        f"{source_message_id}",
    )
    response = client.post(
        "/documents",
        data={"source_message_id": source_message_id},
        files={"file": ("invoice.pdf", pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 202
    document_id = response.json()["document_id"]
    assert isinstance(document_id, str)
    status = client.get(f"/documents/{document_id}").json()
    assert status["status"] == "needs_review"
    return document_id


def test_needs_review_document_appears_in_review_listing(
    ingestion_client: TestClient,
) -> None:
    document_id = _upload_needs_review(ingestion_client, "msg-review-list-1")
    listing = ingestion_client.get("/reviews")
    assert listing.status_code == 200
    ids = {item["document_id"] for item in listing.json()["items"]}
    assert document_id in ids
    detail = ingestion_client.get(f"/reviews/{document_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "needs_review"
    assert body["extraction"] is not None
    assert body["source_available"] is True
    source = ingestion_client.get(f"/documents/{document_id}/source")
    assert source.status_code == 200
    assert source.headers["content-type"].startswith("application/pdf")


def test_edit_and_approve_submits_exactly_one_erp_invoice(
    ingestion_client: TestClient,
    erp_client: ErpClient,
    migrated_db: sessionmaker[Session],
) -> None:
    document_id = _upload_needs_review(ingestion_client, "msg-review-edit-approve")
    before = erp_client._client.get("/_debug/invoice_count").json()["count"]
    edited = _valid_invoice(
        invoice_number=f"INV-EDIT-{document_id[:8]}",
        overall_confidence=0.97,
    )
    response = ingestion_client.post(
        f"/reviews/{document_id}/approve",
        json={
            "actor": "reviewer-anna",
            "reason": "Corrected confidence and totals",
            "edited_invoice": edited.model_dump(mode="json"),
        },
    )
    assert response.status_code == 202
    assert response.json()["status"] == "submitted"
    assert response.json()["resumed"] is True
    status = ingestion_client.get(f"/documents/{document_id}").json()
    assert status["status"] == "submitted"
    assert status["decision"]["actor"] == "reviewer-anna"
    after = erp_client._client.get("/_debug/invoice_count").json()["count"]
    assert after == before + 1
    with session_scope(migrated_db) as session:
        audits = session.scalars(
            select(AuditLogRecord).where(
                AuditLogRecord.document_id == UUID(document_id),
                AuditLogRecord.action == "review_edit_approved",
            ),
        ).all()
        assert len(audits) == 1
        assert audits[0].actor == "reviewer-anna"
        assert "before" in audits[0].details_json
        assert "after" in audits[0].details_json
        rows = session.scalars(
            select(SubmittedInvoiceRecord).where(
                SubmittedInvoiceRecord.document_id == UUID(document_id),
            ),
        ).all()
        assert len(rows) == 1


def test_reject_never_calls_erp(
    ingestion_client: TestClient,
    erp_client: ErpClient,
    migrated_db: sessionmaker[Session],
) -> None:
    document_id = _upload_needs_review(ingestion_client, "msg-review-reject")
    before = erp_client._client.get("/_debug/invoice_count").json()["count"]
    response = ingestion_client.post(
        f"/reviews/{document_id}/reject",
        json={"actor": "reviewer-bob", "reason": "Duplicate supplier claim"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    status = ingestion_client.get(f"/documents/{document_id}").json()
    assert status["status"] == "rejected"
    after = erp_client._client.get("/_debug/invoice_count").json()["count"]
    assert after == before
    with session_scope(migrated_db) as session:
        audits = session.scalars(
            select(AuditLogRecord).where(
                AuditLogRecord.document_id == UUID(document_id),
                AuditLogRecord.action == "review_rejected",
            ),
        ).all()
        assert len(audits) == 1
        assert audits[0].details_json["reason"] == "Duplicate supplier claim"
        rows = session.scalars(
            select(SubmittedInvoiceRecord).where(
                SubmittedInvoiceRecord.document_id == UUID(document_id),
            ),
        ).all()
        assert rows == []


def test_duplicate_approve_does_not_create_second_erp_invoice(
    ingestion_client: TestClient,
    erp_client: ErpClient,
) -> None:
    document_id = _upload_needs_review(ingestion_client, "msg-review-dup-approve")
    edited = _valid_invoice(
        invoice_number=f"INV-DUP-{document_id[:8]}",
        overall_confidence=0.98,
    )
    payload = {
        "actor": "reviewer-cara",
        "reason": "Approved after correction",
        "edited_invoice": edited.model_dump(mode="json"),
    }
    before = erp_client._client.get("/_debug/invoice_count").json()["count"]
    first = ingestion_client.post(f"/reviews/{document_id}/approve", json=payload)
    second = ingestion_client.post(f"/reviews/{document_id}/approve", json=payload)
    assert first.status_code == 202
    assert first.json()["status"] == "submitted"
    assert second.status_code == 202
    assert second.json()["idempotent_replay"] is True
    after = erp_client._client.get("/_debug/invoice_count").json()["count"]
    assert after == before + 1


def test_invalid_edit_stays_needs_review_without_erp(
    ingestion_client: TestClient,
    erp_client: ErpClient,
) -> None:
    document_id = _upload_needs_review(ingestion_client, "msg-review-invalid-edit")
    before = erp_client._client.get("/_debug/invoice_count").json()["count"]
    bad = _valid_invoice(
        invoice_number=f"INV-BAD-{document_id[:8]}",
        total_amount=Decimal("999.99"),
        overall_confidence=0.99,
    )
    response = ingestion_client.post(
        f"/reviews/{document_id}/approve",
        json={
            "actor": "reviewer-dan",
            "reason": "Attempted invalid totals",
            "edited_invoice": bad.model_dump(mode="json"),
        },
    )
    assert response.status_code == 422
    status = ingestion_client.get(f"/documents/{document_id}").json()
    assert status["status"] == "needs_review"
    after = erp_client._client.get("/_debug/invoice_count").json()["count"]
    assert after == before


def test_approve_without_edits_resumes_and_submits(
    ingestion_client: TestClient,
    erp_client: ErpClient,
) -> None:
    document_id = _upload_needs_review(ingestion_client, "msg-review-plain-approve")
    before = erp_client._client.get("/_debug/invoice_count").json()["count"]
    response = ingestion_client.post(
        f"/reviews/{document_id}/approve",
        json={"actor": "reviewer-erin", "reason": "Accept extraction as-is"},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "submitted"
    after = erp_client._client.get("/_debug/invoice_count").json()["count"]
    assert after == before + 1


def test_terminal_reject_conflict(
    settings: Settings,
    migrated_db: sessionmaker[Session],
    erp_client: ErpClient,
    fake_provider: FakeLLMProvider,
) -> None:
    def enqueue(document_id: UUID, correlation_id: UUID) -> None:
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

    app = create_app(
        settings,
        session_factory=migrated_db,
        enqueue_document=enqueue,
        erp_client=erp_client,
    )
    with TestClient(app) as client:
        document_id = _upload_needs_review(client, "msg-review-terminal")
        first = client.post(
            f"/reviews/{document_id}/reject",
            json={"actor": "reviewer-fin", "reason": "Not valid"},
        )
        assert first.status_code == 200
        second = client.post(
            f"/reviews/{document_id}/approve",
            json={"actor": "reviewer-fin", "reason": "Too late"},
        )
        assert second.status_code == 409


def test_domain_reject_idempotent(
    settings: Settings,
    migrated_db: sessionmaker[Session],
    erp_client: ErpClient,
    fake_provider: FakeLLMProvider,
) -> None:
    def enqueue(document_id: UUID, correlation_id: UUID) -> None:
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

    app = create_app(
        settings,
        session_factory=migrated_db,
        enqueue_document=enqueue,
        erp_client=erp_client,
    )
    with TestClient(app) as client:
        document_id = _upload_needs_review(client, "msg-review-domain-idem")
    with session_scope(migrated_db) as session:
        first, changed_first = reject_review(
            session,
            document_id=UUID(document_id),
            actor="reviewer-gia",
            reason="Reject once",
        )
        assert changed_first is True
        assert first.status == "rejected"
        second, changed_second = reject_review(
            session,
            document_id=UUID(document_id),
            actor="reviewer-gia",
            reason="Reject again",
        )
        assert changed_second is False
        assert second.status == "rejected"
        with pytest.raises(ReviewConflictError):
            approve_review(
                session,
                document_id=UUID(document_id),
                actor="reviewer-gia",
                reason="Cannot approve",
                settings=settings,
                erp_client=erp_client,
            )
