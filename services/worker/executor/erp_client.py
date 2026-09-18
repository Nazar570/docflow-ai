from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field


class ErpSupplier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_id: str
    name: str


class ErpPurchaseOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purchase_order_id: str
    supplier_id: str
    currency: str
    amount: Decimal


class ErpInvoiceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_id: str
    purchase_order_id: str | None = None
    invoice_number: str
    invoice_date: str
    currency: str
    total_amount: Decimal
    line_items: list[dict[str, Any]] = Field(default_factory=list)


class ErpInvoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_id: str
    supplier_id: str
    purchase_order_id: str | None = None
    invoice_number: str
    invoice_date: str
    currency: str
    total_amount: Decimal
    idempotency_key: str


class ErpClientError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ErpGateway(Protocol):
    def get_supplier(self, supplier_id: str) -> ErpSupplier | None: ...

    def get_purchase_order(self, purchase_order_id: str) -> ErpPurchaseOrder | None: ...

    def create_invoice(
        self,
        payload: ErpInvoiceCreateRequest,
        *,
        idempotency_key: str,
    ) -> ErpInvoice: ...


class ErpClient:
    def __init__(self, *, base_url: str, timeout_seconds: float) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout_seconds)

    def close(self) -> None:
        self._client.close()

    def get_supplier(self, supplier_id: str) -> ErpSupplier | None:
        response = self._client.get(f"/suppliers/{supplier_id}")
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise ErpClientError(
                "Supplier lookup failed",
                status_code=response.status_code,
            )
        return ErpSupplier.model_validate(response.json())

    def get_purchase_order(self, purchase_order_id: str) -> ErpPurchaseOrder | None:
        response = self._client.get(f"/purchase_orders/{purchase_order_id}")
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise ErpClientError(
                "Purchase order lookup failed",
                status_code=response.status_code,
            )
        return ErpPurchaseOrder.model_validate(response.json())

    def create_invoice(
        self,
        payload: ErpInvoiceCreateRequest,
        *,
        idempotency_key: str,
    ) -> ErpInvoice:
        response = self._client.post(
            "/invoices",
            json=payload.model_dump(mode="json"),
            headers={"Idempotency-Key": idempotency_key},
        )
        if response.status_code >= 400:
            raise ErpClientError(
                "ERP invoice create failed",
                status_code=response.status_code,
            )
        return ErpInvoice.model_validate(response.json())


def build_erp_idempotency_key(*, document_id: str, invoice_number: str) -> str:
    return f"{document_id}:{invoice_number}"


def new_correlation_id() -> str:
    return str(uuid4())
