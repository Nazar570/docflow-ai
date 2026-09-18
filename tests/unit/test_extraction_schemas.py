from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from services.worker.extraction.prompt_versions import (
    PROMPT_VERSION,
    load_prompt_template,
)
from services.worker.extraction.schemas import Invoice, parse_invoice_payload


def test_parse_valid_invoice_payload() -> None:
    invoice = parse_invoice_payload(
        {
            "supplier_name": "Acme Supplies GmbH",
            "invoice_number": "INV-1001",
            "invoice_date": "2026-03-15",
            "currency": "EUR",
            "total_amount": "125.50",
            "line_items": [
                {
                    "description": "Paper reams",
                    "quantity": "5",
                    "unit_price": "10.00",
                    "line_total": "50.00",
                },
            ],
            "overall_confidence": 0.96,
            "field_confidence": {
                "supplier_name": 0.96,
                "invoice_number": 0.97,
                "invoice_date": 0.95,
                "currency": 0.99,
                "total_amount": 0.94,
                "line_items": 0.93,
            },
        },
    )
    assert isinstance(invoice, Invoice)
    assert invoice.supplier_name == "Acme Supplies GmbH"
    assert invoice.invoice_number == "INV-1001"
    assert invoice.invoice_date == date(2026, 3, 15)
    assert invoice.currency == "EUR"
    assert invoice.total_amount == Decimal("125.50")
    assert len(invoice.line_items) == 1
    assert invoice.overall_confidence == 0.96
    assert invoice.field_confidence.invoice_number == 0.97


def test_schema_rejects_malformed_total_amount() -> None:
    with pytest.raises(ValidationError):
        parse_invoice_payload(
            {
                "supplier_name": "Acme Supplies GmbH",
                "invoice_number": "INV-1001",
                "invoice_date": "2026-03-15",
                "currency": "EUR",
                "total_amount": "not-a-number",
                "line_items": [],
                "overall_confidence": 0.95,
            },
        )


def test_schema_rejects_missing_required_fields() -> None:
    with pytest.raises(ValidationError):
        parse_invoice_payload(
            {
                "supplier_name": "Acme Supplies GmbH",
                "currency": "EUR",
                "total_amount": "100.00",
                "overall_confidence": 0.91,
            },
        )


def test_schema_rejects_confidence_out_of_range() -> None:
    with pytest.raises(ValidationError):
        parse_invoice_payload(
            {
                "supplier_name": "Acme Supplies GmbH",
                "invoice_number": "INV-1001",
                "invoice_date": "2026-03-15",
                "currency": "EUR",
                "total_amount": "100.00",
                "line_items": [],
                "overall_confidence": 1.5,
            },
        )


def test_prompt_template_is_versioned_and_loadable() -> None:
    template = load_prompt_template()
    assert PROMPT_VERSION == "invoice_extraction_v1"
    assert "invoice" in template.lower()
    assert len(template.strip()) > 0
