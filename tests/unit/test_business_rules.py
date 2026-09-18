from datetime import date
from decimal import Decimal

import pytest

from services.ingestion.idempotency import build_idempotency_key, content_sha256
from services.worker.executor.erp_client import build_erp_idempotency_key
from services.worker.extraction.schemas import Invoice, InvoiceLineItem
from services.worker.rules.business_rules import (
    DecisionType,
    RuleContext,
    decide_workflow,
    validate_invoice,
)
from services.worker.rules.states import (
    IllegalStateTransitionError,
    WorkflowState,
    assert_transition_allowed,
)


def _invoice(**overrides: object) -> Invoice:
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


def _context(**overrides: object) -> RuleContext:
    payload: dict[str, object] = {
        "supported_currencies": ["EUR", "USD", "PLN"],
        "auto_approval_confidence_threshold": 0.90,
        "auto_approval_limit": Decimal("1000.00"),
        "totals_tolerance": Decimal("0.01"),
        "supplier_exists": True,
        "purchase_order_exists": True,
        "is_duplicate": False,
    }
    payload.update(overrides)
    return RuleContext.model_validate(payload)


def test_validate_invoice_passes_for_clean_invoice() -> None:
    result = validate_invoice(_invoice(), _context())
    assert result.is_valid is True
    assert result.issues == []


def test_validate_invoice_flags_unknown_supplier() -> None:
    result = validate_invoice(_invoice(), _context(supplier_exists=False))
    assert result.is_valid is False
    assert any(issue.code == "unknown_supplier" for issue in result.issues)


def test_validate_invoice_flags_invalid_totals() -> None:
    result = validate_invoice(
        _invoice(total_amount=Decimal("999.99")),
        _context(),
    )
    assert result.is_valid is False
    assert any(issue.code == "total_mismatch" for issue in result.issues)


def test_validate_invoice_flags_unsupported_currency() -> None:
    result = validate_invoice(_invoice(currency="GBP"), _context())
    assert result.is_valid is False
    assert any(issue.code == "unsupported_currency" for issue in result.issues)


def test_validate_invoice_flags_low_confidence() -> None:
    result = validate_invoice(_invoice(overall_confidence=0.4), _context())
    assert result.is_valid is False
    assert any(issue.code == "low_confidence" for issue in result.issues)


def test_validate_invoice_flags_duplicate() -> None:
    result = validate_invoice(_invoice(), _context(is_duplicate=True))
    assert result.is_valid is False
    assert any(issue.code == "duplicate_invoice" for issue in result.issues)


def test_decide_workflow_auto_approves_clean_invoice() -> None:
    decision = decide_workflow(_invoice(), _context())
    assert decision.decision is DecisionType.AUTO_APPROVE


def test_decide_workflow_routes_low_confidence_to_review() -> None:
    decision = decide_workflow(_invoice(overall_confidence=0.4), _context())
    assert decision.decision is DecisionType.NEEDS_REVIEW


def test_legal_state_transitions() -> None:
    assert_transition_allowed(WorkflowState.RECEIVED, WorkflowState.QUEUED)
    assert_transition_allowed(WorkflowState.QUEUED, WorkflowState.PROCESSING)
    assert_transition_allowed(WorkflowState.PROCESSING, WorkflowState.EXTRACTED)
    assert_transition_allowed(WorkflowState.EXTRACTED, WorkflowState.APPROVED)
    assert_transition_allowed(WorkflowState.APPROVED, WorkflowState.SUBMITTED)
    assert_transition_allowed(WorkflowState.QUEUED, WorkflowState.FAILED)
    assert_transition_allowed(WorkflowState.PROCESSING, WorkflowState.FAILED)


def test_illegal_state_transition_rejected() -> None:
    with pytest.raises(IllegalStateTransitionError):
        assert_transition_allowed(WorkflowState.SUBMITTED, WorkflowState.PROCESSING)
    with pytest.raises(IllegalStateTransitionError):
        assert_transition_allowed(WorkflowState.RECEIVED, WorkflowState.PROCESSING)
    with pytest.raises(IllegalStateTransitionError):
        assert_transition_allowed(WorkflowState.FAILED, WorkflowState.APPROVED)


def test_idempotency_key_is_stable() -> None:
    content = b"%PDF-sample%"
    digest = content_sha256(content)
    first = build_idempotency_key(source_message_id="msg-1", content_hash=digest)
    second = build_idempotency_key(source_message_id="msg-1", content_hash=digest)
    assert first == second
    assert first != build_idempotency_key(
        source_message_id="msg-2",
        content_hash=digest,
    )


def test_erp_idempotency_key_includes_document_and_invoice() -> None:
    key = build_erp_idempotency_key(
        document_id="11111111-1111-1111-1111-111111111111",
        invoice_number="INV-1001",
    )
    assert key == "11111111-1111-1111-1111-111111111111:INV-1001"
