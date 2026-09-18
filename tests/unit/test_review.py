from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from services.ingestion.api_schemas import ReviewActionRequest
from services.worker.extraction.schemas import Invoice, InvoiceLineItem
from services.worker.rules.states import (
    IllegalStateTransitionError,
    WorkflowState,
    assert_transition_allowed,
)


def _valid_invoice(**overrides: object) -> Invoice:
    payload: dict[str, object] = {
        "supplier_name": "Acme Supplies GmbH",
        "supplier_id": "sup-acme-001",
        "purchase_order_id": "po-1001",
        "invoice_number": "INV-REVIEW-1",
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


def test_review_action_requires_actor_and_reason() -> None:
    with pytest.raises(ValidationError):
        ReviewActionRequest.model_validate({"actor": "", "reason": "ok"})
    with pytest.raises(ValidationError):
        ReviewActionRequest.model_validate({"actor": "reviewer", "reason": ""})
    request = ReviewActionRequest.model_validate(
        {"actor": "reviewer-1", "reason": "Looks correct"},
    )
    assert request.actor == "reviewer-1"
    assert request.edited_invoice is None


def test_review_action_accepts_typed_edited_invoice() -> None:
    request = ReviewActionRequest.model_validate(
        {
            "actor": "reviewer-1",
            "reason": "Corrected confidence",
            "edited_invoice": _valid_invoice().model_dump(mode="json"),
        },
    )
    assert request.edited_invoice is not None
    assert request.edited_invoice.overall_confidence == 0.96


def test_review_action_rejects_invalid_edited_invoice() -> None:
    with pytest.raises(ValidationError):
        ReviewActionRequest.model_validate(
            {
                "actor": "reviewer-1",
                "reason": "bad edit",
                "edited_invoice": {
                    "supplier_name": "Acme",
                    "invoice_number": "INV-1",
                    "invoice_date": "2026-03-15",
                    "currency": "EUR",
                    "total_amount": "not-a-number",
                    "overall_confidence": 0.9,
                },
            },
        )


def test_legal_review_state_path() -> None:
    assert_transition_allowed(WorkflowState.NEEDS_REVIEW, WorkflowState.APPROVED)
    assert_transition_allowed(WorkflowState.APPROVED, WorkflowState.SUBMITTED)
    assert_transition_allowed(WorkflowState.NEEDS_REVIEW, WorkflowState.REJECTED)


def test_terminal_states_reject_review_commands() -> None:
    for state in {
        WorkflowState.SUBMITTED,
        WorkflowState.REJECTED,
        WorkflowState.FAILED,
    }:
        with pytest.raises(IllegalStateTransitionError):
            assert_transition_allowed(state, WorkflowState.APPROVED)
        with pytest.raises(IllegalStateTransitionError):
            assert_transition_allowed(state, WorkflowState.REJECTED)


def test_stale_needs_review_cannot_go_directly_to_submitted() -> None:
    with pytest.raises(IllegalStateTransitionError):
        assert_transition_allowed(WorkflowState.NEEDS_REVIEW, WorkflowState.SUBMITTED)


def test_invoice_helper_unique_number() -> None:
    invoice = _valid_invoice(invoice_number=f"INV-{uuid4().hex[:8]}")
    assert invoice.supplier_id == "sup-acme-001"
