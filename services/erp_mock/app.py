from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from services.observability import metrics as obs_metrics
from services.observability.logging import configure_structured_logging
from services.observability.tracing import instrument_fastapi, setup_tracing


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str


class SupplierResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_id: str
    name: str


class PurchaseOrderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purchase_order_id: str
    supplier_id: str
    currency: str
    amount: Decimal


class InvoiceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_id: str
    purchase_order_id: str | None = None
    invoice_number: str
    invoice_date: str
    currency: str
    total_amount: Decimal
    line_items: list[dict[str, Any]] = Field(default_factory=list)


class InvoiceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_id: str
    supplier_id: str
    purchase_order_id: str | None = None
    invoice_number: str
    invoice_date: str
    currency: str
    total_amount: Decimal
    idempotency_key: str


def seed_suppliers() -> dict[str, SupplierResponse]:
    return {
        "sup-acme-001": SupplierResponse(
            supplier_id="sup-acme-001",
            name="Acme Supplies GmbH",
        ),
    }


def seed_purchase_orders() -> dict[str, PurchaseOrderResponse]:
    return {
        "po-1001": PurchaseOrderResponse(
            purchase_order_id="po-1001",
            supplier_id="sup-acme-001",
            currency="EUR",
            amount=Decimal("125.50"),
        ),
    }


def create_app() -> FastAPI:
    configure_structured_logging(service="erp_mock")
    setup_tracing(service_name="docflow-erp-mock")
    app = FastAPI(title="DocFlow AI ERP Mock")
    instrument_fastapi(app)
    suppliers = seed_suppliers()
    purchase_orders = seed_purchase_orders()
    invoices_by_id: dict[str, InvoiceResponse] = {}
    invoices_by_idempotency: dict[str, InvoiceResponse] = {}

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(
            content=obs_metrics.metrics_payload(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.get("/suppliers/{supplier_id}", response_model=SupplierResponse)
    def get_supplier(supplier_id: str) -> SupplierResponse:
        supplier = suppliers.get(supplier_id)
        if supplier is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Supplier not found",
            )
        return supplier

    @app.get(
        "/purchase_orders/{purchase_order_id}",
        response_model=PurchaseOrderResponse,
    )
    def get_purchase_order(purchase_order_id: str) -> PurchaseOrderResponse:
        purchase_order = purchase_orders.get(purchase_order_id)
        if purchase_order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Purchase order not found",
            )
        return purchase_order

    @app.post("/invoices", response_model=InvoiceResponse)
    def create_invoice(
        payload: InvoiceCreateRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> InvoiceResponse:
        if not idempotency_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Idempotency-Key header is required",
            )
        existing = invoices_by_idempotency.get(idempotency_key)
        if existing is not None:
            return existing
        if payload.supplier_id not in suppliers:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unknown supplier_id",
            )
        invoice = InvoiceResponse(
            invoice_id=str(uuid4()),
            supplier_id=payload.supplier_id,
            purchase_order_id=payload.purchase_order_id,
            invoice_number=payload.invoice_number,
            invoice_date=payload.invoice_date,
            currency=payload.currency,
            total_amount=payload.total_amount,
            idempotency_key=idempotency_key,
        )
        invoices_by_id[invoice.invoice_id] = invoice
        invoices_by_idempotency[idempotency_key] = invoice
        return invoice

    @app.get("/invoices/{invoice_id}", response_model=InvoiceResponse)
    def get_invoice(invoice_id: str) -> InvoiceResponse:
        invoice = invoices_by_id.get(invoice_id)
        if invoice is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Invoice not found",
            )
        return invoice

    @app.get("/_debug/invoice_count")
    def invoice_count() -> dict[str, int]:
        return {"count": len(invoices_by_id)}

    return app


app = create_app()
