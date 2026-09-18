from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class InvoiceLineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1)
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal

    @field_validator("quantity", "unit_price", "line_total", mode="before")
    @classmethod
    def parse_decimal(cls, value: Any) -> Any:
        if isinstance(value, float):
            return str(value)
        return value


class FieldConfidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_name: float | None = Field(default=None, ge=0.0, le=1.0)
    invoice_number: float | None = Field(default=None, ge=0.0, le=1.0)
    invoice_date: float | None = Field(default=None, ge=0.0, le=1.0)
    currency: float | None = Field(default=None, ge=0.0, le=1.0)
    total_amount: float | None = Field(default=None, ge=0.0, le=1.0)
    line_items: float | None = Field(default=None, ge=0.0, le=1.0)


class Invoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_name: str = Field(min_length=1)
    invoice_number: str = Field(min_length=1)
    invoice_date: date
    currency: str = Field(min_length=1)
    total_amount: Decimal
    line_items: list[InvoiceLineItem] = Field(default_factory=list)
    overall_confidence: float = Field(ge=0.0, le=1.0)
    field_confidence: FieldConfidence = Field(default_factory=FieldConfidence)
    supplier_id: str | None = None
    purchase_order_id: str | None = None

    @field_validator("total_amount", mode="before")
    @classmethod
    def parse_total_amount(cls, value: Any) -> Any:
        if isinstance(value, float):
            return str(value)
        return value


def parse_invoice_payload(payload: dict[str, Any]) -> Invoice:
    return Invoice.model_validate(payload)
