from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from services.erp_mock.app import create_app as create_erp_app
from services.worker.executor.erp_client import build_erp_idempotency_key

pytestmark = pytest.mark.integration


def test_erp_mock_supplier_and_po_lookup() -> None:
    app = create_erp_app()
    with TestClient(app) as client:
        supplier = client.get("/suppliers/sup-acme-001")
        assert supplier.status_code == 200
        assert supplier.json()["name"] == "Acme Supplies GmbH"
        missing = client.get("/suppliers/missing")
        assert missing.status_code == 404
        purchase_order = client.get("/purchase_orders/po-1001")
        assert purchase_order.status_code == 200


def test_erp_mock_invoice_create_is_idempotent() -> None:
    app = create_erp_app()
    with TestClient(app) as client:
        payload = {
            "supplier_id": "sup-acme-001",
            "purchase_order_id": "po-1001",
            "invoice_number": "INV-1001",
            "invoice_date": "2026-03-15",
            "currency": "EUR",
            "total_amount": str(Decimal("125.50")),
            "line_items": [],
        }
        key = build_erp_idempotency_key(
            document_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            invoice_number="INV-1001",
        )
        headers = {"Idempotency-Key": key}
        first = client.post("/invoices", json=payload, headers=headers)
        second = client.post("/invoices", json=payload, headers=headers)
        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["invoice_id"] == second.json()["invoice_id"]
        count = client.get("/_debug/invoice_count")
        assert count.json()["count"] == 1


def test_ingestion_health(ingestion_client: TestClient) -> None:
    response = ingestion_client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_pdf_upload_rejects_non_pdf(ingestion_client: TestClient) -> None:
    response = ingestion_client.post(
        "/documents",
        data={"source_message_id": "msg-bad"},
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400
