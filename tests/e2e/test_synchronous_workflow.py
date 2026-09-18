import pytest
from fastapi.testclient import TestClient

from services.worker.executor.erp_client import ErpClient
from services.worker.extraction.pdf_samples import build_pdf_bytes

pytestmark = pytest.mark.e2e


def test_upload_success_reaches_submitted(ingestion_client: TestClient) -> None:
    pdf_bytes = build_pdf_bytes(
        "Fictional invoice for Acme Supplies GmbH INV-1001 EUR 125.50",
    )
    response = ingestion_client.post(
        "/documents",
        data={"source_message_id": "msg-success-001"},
        files={"file": ("invoice.pdf", pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] in {"queued", "submitted"}
    document_id = body["document_id"]
    status_response = ingestion_client.get(f"/documents/{document_id}")
    assert status_response.status_code == 200
    status_body = status_response.json()
    assert status_body["status"] == "submitted"
    assert status_body["decision"]["decision"] == "auto_approve"
    assert len(status_body["audit_summary"]) >= 1
    assert "raw_path" not in status_body


def test_upload_low_confidence_reaches_needs_review_without_erp_invoice(
    ingestion_client: TestClient,
    erp_client: ErpClient,
) -> None:
    pdf_bytes = build_pdf_bytes(
        "DOC FLOW SCENARIO:LOW_CONFIDENCE fictional low confidence invoice",
    )
    before = erp_client._client.get("/_debug/invoice_count").json()["count"]
    response = ingestion_client.post(
        "/documents",
        data={"source_message_id": "msg-review-001"},
        files={"file": ("invoice.pdf", pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 202
    document_id = response.json()["document_id"]
    status_body = ingestion_client.get(f"/documents/{document_id}").json()
    assert status_body["status"] == "needs_review"
    assert status_body["decision"]["decision"] == "needs_review"
    after = erp_client._client.get("/_debug/invoice_count").json()["count"]
    assert after == before


def test_repeated_identical_upload_is_idempotent(
    ingestion_client: TestClient,
    erp_client: ErpClient,
) -> None:
    pdf_bytes = build_pdf_bytes(
        "Fictional invoice for Acme Supplies GmbH INV-1001 EUR 125.50 idempotent",
    )
    files = {"file": ("invoice.pdf", pdf_bytes, "application/pdf")}
    data = {"source_message_id": "msg-idempotent-001"}
    first = ingestion_client.post("/documents", data=data, files=files)
    assert first.status_code == 202
    first_id = first.json()["document_id"]
    status_body = ingestion_client.get(f"/documents/{first_id}").json()
    assert status_body["status"] == "submitted"
    count_after_first = erp_client._client.get("/_debug/invoice_count").json()["count"]
    second = ingestion_client.post(
        "/documents",
        data=data,
        files={"file": ("invoice.pdf", pdf_bytes, "application/pdf")},
    )
    assert second.status_code == 202
    assert second.json()["idempotent_replay"] is True
    assert second.json()["document_id"] == first_id
    count_after_second = erp_client._client.get("/_debug/invoice_count").json()["count"]
    assert count_after_second == count_after_first
