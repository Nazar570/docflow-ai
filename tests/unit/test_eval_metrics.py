from datetime import date
from decimal import Decimal

from llmops.eval.metrics import (
    aggregate_metrics,
    percentile_nearest_rank,
    safe_rate,
    score_fields,
)
from llmops.eval.normalize import (
    normalize_currency,
    normalize_date,
    normalize_decimal,
    normalize_line_items,
    normalize_text,
    values_equal,
)


def test_normalize_text_trims_and_casefolds() -> None:
    assert normalize_text("  Acme   Supplies ") == "acme supplies"
    assert normalize_text(None) is None


def test_normalize_decimal_and_currency_and_date() -> None:
    assert normalize_decimal("125.50") == Decimal("125.50")
    assert normalize_decimal("bad") is None
    assert normalize_currency(" eur ") == "EUR"
    assert normalize_date("2026-03-15") == date(2026, 3, 15)
    assert normalize_date("nope") is None


def test_normalize_line_items_order_independent() -> None:
    left = normalize_line_items(
        [
            {
                "description": "B",
                "quantity": "1",
                "unit_price": "2.00",
                "line_total": "2.00",
            },
            {
                "description": "A",
                "quantity": "1",
                "unit_price": "1.00",
                "line_total": "1.00",
            },
        ],
    )
    right = normalize_line_items(
        [
            {
                "description": "A",
                "quantity": "1",
                "unit_price": "1.00",
                "line_total": "1.00",
            },
            {
                "description": "B",
                "quantity": "1",
                "unit_price": "2.00",
                "line_total": "2.00",
            },
        ],
    )
    assert left == right


def test_values_equal_for_core_fields() -> None:
    assert values_equal("supplier_name", "Acme Supplies", "  acme   supplies ")
    assert values_equal("total_amount", "125.50", Decimal("125.50"))
    assert not values_equal("invoice_number", "INV-1", "INV-2")
    assert values_equal("invoice_date", "2026-03-15", date(2026, 3, 15))


def test_safe_rate_and_percentile_zero_and_single() -> None:
    assert safe_rate(1, 0) is None
    assert safe_rate(1, 2) == 0.5
    assert percentile_nearest_rank([], 95.0) is None
    assert percentile_nearest_rank([10.0], 95.0) == 10.0
    assert percentile_nearest_rank([1.0, 2.0, 3.0, 4.0], 95.0) == 4.0


def test_score_fields_counts_correct_and_absent() -> None:
    scores = score_fields(
        {
            "supplier_name": "Acme",
            "invoice_number": "INV-1",
            "total_amount": "10.00",
        },
        {
            "supplier_name": "acme",
            "invoice_number": "INV-2",
            "total_amount": "10.00",
        },
        applicable=True,
    )
    assert scores["supplier_name"].correct == 1
    assert scores["invoice_number"].correct == 0
    assert scores["invoice_number"].compared == 1
    skipped = score_fields({"supplier_name": "Acme"}, None, applicable=False)
    assert skipped["supplier_name"].compared == 0


def test_wrong_extraction_lowers_field_accuracy() -> None:
    good = score_fields(
        {"invoice_number": "INV-1", "total_amount": "10.00"},
        {"invoice_number": "INV-1", "total_amount": "10.00"},
        applicable=True,
    )
    bad = score_fields(
        {"invoice_number": "INV-1", "total_amount": "10.00"},
        {"invoice_number": "INV-BAD", "total_amount": "1.00"},
        applicable=True,
    )
    good_rate = sum(s.correct for s in good.values()) / sum(
        s.compared for s in good.values()
    )
    bad_rate = sum(s.correct for s in bad.values()) / sum(
        s.compared for s in bad.values()
    )
    assert bad_rate < good_rate


def test_aggregate_metrics_decision_and_provider_failure() -> None:
    metrics = aggregate_metrics(
        case_results=[
            {
                "schema_applicable": True,
                "schema_valid": True,
                "field_comparison_applicable": True,
                "field_scores": {
                    "invoice_number": {"compared": 1, "correct": 1},
                },
                "decision_applicable": True,
                "decision_match": True,
                "case_success": True,
                "outcome_class": "ok",
                "latency_ms": 1.0,
            },
            {
                "schema_applicable": True,
                "schema_valid": False,
                "field_comparison_applicable": False,
                "field_scores": {},
                "decision_applicable": True,
                "decision_match": False,
                "case_success": False,
                "outcome_class": "provider_failure",
                "expected_error_class": "timeout",
                "latency_ms": 2.0,
            },
        ],
    )
    assert metrics.decision_accuracy == 0.5
    assert metrics.provider_failure_count == 1
    assert metrics.schema_validity_rate == 0.5
