from decimal import Decimal
from statistics import mean
from typing import Any

from pydantic import BaseModel, ConfigDict

from llmops.eval.normalize import values_equal

COMPARISON_FIELDS = [
    "supplier_name",
    "supplier_id",
    "purchase_order_id",
    "invoice_number",
    "invoice_date",
    "currency",
    "total_amount",
    "line_items",
    "overall_confidence",
]


class FieldScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    compared: int = 0
    correct: int = 0

    @property
    def accuracy(self) -> float | None:
        if self.compared == 0:
            return None
        return self.correct / self.compared


class AggregateMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_validity_rate: float | None
    schema_validity_numerator: int
    schema_validity_denominator: int
    field_accuracy: float | None
    field_accuracy_numerator: int
    field_accuracy_denominator: int
    per_field_accuracy: dict[str, float | None]
    decision_accuracy: float | None
    decision_accuracy_numerator: int
    decision_accuracy_denominator: int
    case_success_rate: float | None
    case_success_numerator: int
    case_success_denominator: int
    provider_failure_count: int
    expected_provider_failure_count: int
    unexpected_provider_failure_count: int
    unexpected_evaluator_error_count: int
    latency_ms_mean: float | None
    latency_ms_p95: float | None
    latency_sample_count: int


def safe_rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def percentile_nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(1, int(round((percentile / 100.0) * len(ordered))))
    index = min(len(ordered), rank) - 1
    return ordered[index]


def score_fields(
    expected: dict[str, Any] | None,
    actual: dict[str, Any] | None,
    *,
    applicable: bool,
) -> dict[str, FieldScore]:
    scores = {field: FieldScore(field=field) for field in COMPARISON_FIELDS}
    if not applicable or expected is None or actual is None:
        return scores
    for field in COMPARISON_FIELDS:
        if field not in expected:
            continue
        scores[field].compared += 1
        if values_equal(field, expected.get(field), actual.get(field)):
            scores[field].correct += 1
    return scores


def aggregate_metrics(
    *,
    case_results: list[dict[str, Any]],
) -> AggregateMetrics:
    schema_num = 0
    schema_den = 0
    field_num = 0
    field_den = 0
    per_field: dict[str, FieldScore] = {
        field: FieldScore(field=field) for field in COMPARISON_FIELDS
    }
    decision_num = 0
    decision_den = 0
    success_num = 0
    success_den = len(case_results)
    provider_failure_count = 0
    expected_provider_failure_count = 0
    unexpected_provider_failure_count = 0
    unexpected_evaluator_error_count = 0
    latencies: list[float] = []

    for case in case_results:
        if case.get("schema_applicable"):
            schema_den += 1
            if case.get("schema_valid") is True:
                schema_num += 1
        if case.get("field_comparison_applicable"):
            field_scores = case.get("field_scores", {})
            for field, score in field_scores.items():
                compared = int(score.get("compared", 0))
                correct = int(score.get("correct", 0))
                per_field[field].compared += compared
                per_field[field].correct += correct
                field_den += compared
                field_num += correct
        if case.get("decision_applicable"):
            decision_den += 1
            if case.get("decision_match") is True:
                decision_num += 1
        if case.get("case_success") is True:
            success_num += 1
        if case.get("outcome_class") == "provider_failure":
            provider_failure_count += 1
            if case.get("expected_error_class"):
                expected_provider_failure_count += 1
            else:
                unexpected_provider_failure_count += 1
        if case.get("outcome_class") == "unexpected_evaluator_error":
            unexpected_evaluator_error_count += 1
        latency = case.get("latency_ms")
        if isinstance(latency, (int, float)):
            latencies.append(float(latency))

    return AggregateMetrics(
        schema_validity_rate=safe_rate(schema_num, schema_den),
        schema_validity_numerator=schema_num,
        schema_validity_denominator=schema_den,
        field_accuracy=safe_rate(field_num, field_den),
        field_accuracy_numerator=field_num,
        field_accuracy_denominator=field_den,
        per_field_accuracy={
            field: score.accuracy for field, score in per_field.items()
        },
        decision_accuracy=safe_rate(decision_num, decision_den),
        decision_accuracy_numerator=decision_num,
        decision_accuracy_denominator=decision_den,
        case_success_rate=safe_rate(success_num, success_den),
        case_success_numerator=success_num,
        case_success_denominator=success_den,
        provider_failure_count=provider_failure_count,
        expected_provider_failure_count=expected_provider_failure_count,
        unexpected_provider_failure_count=unexpected_provider_failure_count,
        unexpected_evaluator_error_count=unexpected_evaluator_error_count,
        latency_ms_mean=(mean(latencies) if latencies else None),
        latency_ms_p95=percentile_nearest_rank(latencies, 95.0),
        latency_sample_count=len(latencies),
    )


def decimal_ratio(numerator: int, denominator: int) -> Decimal | None:
    rate = safe_rate(numerator, denominator)
    if rate is None:
        return None
    return Decimal(str(rate))
