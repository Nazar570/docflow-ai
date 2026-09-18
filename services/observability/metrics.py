import logging
from typing import Any

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Histogram,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

from services.observability.labels import (
    bounded_error_class,
    bounded_final_state,
    bounded_provider,
    bounded_validation_category,
    model_family,
)

_REGISTRY = CollectorRegistry()
_DLQ_REDIS_URL: str | None = None


class DlqDepthCollector(Collector):
    def collect(self) -> list[GaugeMetricFamily]:
        value = 0.0
        if _DLQ_REDIS_URL:
            try:
                from services.worker.dlq import DeadLetterQueue

                dlq = DeadLetterQueue(redis_url=_DLQ_REDIS_URL)
                try:
                    value = float(dlq.depth())
                finally:
                    dlq.close()
            except Exception:
                value = 0.0
        metric = GaugeMetricFamily(
            "docflow_dlq_depth",
            "Current Redis DLQ depth",
        )
        metric.add_metric([], value)
        return [metric]


def configure_dlq_collector(redis_url: str) -> None:
    global _DLQ_REDIS_URL
    _DLQ_REDIS_URL = redis_url

DOCUMENTS_INGESTED = Counter(
    "docflow_documents_ingested_total",
    "Documents accepted for processing",
    ["service"],
    registry=_REGISTRY,
)
DOCUMENTS_COMPLETED = Counter(
    "docflow_documents_completed_total",
    "Documents reaching an observable outcome state",
    ["final_state"],
    registry=_REGISTRY,
)
WORKFLOW_DURATION = Histogram(
    "docflow_workflow_duration_seconds",
    "End-to-end duration from document creation to outcome",
    ["final_state"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
    registry=_REGISTRY,
)
EXTRACTION_DURATION = Histogram(
    "docflow_extraction_duration_seconds",
    "Provider extraction duration",
    ["provider", "model_family"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 15.0, 60.0),
    registry=_REGISTRY,
)
VALIDATION_FAILURES = Counter(
    "docflow_validation_failures_total",
    "Validation issue counts by category",
    ["category"],
    registry=_REGISTRY,
)
PROVIDER_REQUESTS = Counter(
    "docflow_provider_requests_total",
    "Provider extraction attempts by outcome",
    ["provider", "outcome"],
    registry=_REGISTRY,
)
PROVIDER_FAILURES = Counter(
    "docflow_provider_failures_total",
    "Provider failures by error class",
    ["provider", "error_class"],
    registry=_REGISTRY,
)
PROVIDER_FALLBACKS = Counter(
    "docflow_provider_fallback_total",
    "Provider fallback events",
    ["from_provider", "to_provider"],
    registry=_REGISTRY,
)
PROVIDER_USAGE = Counter(
    "docflow_provider_usage_total",
    "Successful provider usage by bounded model family",
    ["provider", "model_family"],
    registry=_REGISTRY,
)
RETRIES = Counter(
    "docflow_retries_total",
    "Transient worker retries",
    ["operation", "error_class"],
    registry=_REGISTRY,
)
_REGISTRY.register(DlqDepthCollector())
AUTO_APPROVALS = Counter(
    "docflow_auto_approvals_total",
    "Auto-approval decisions",
    registry=_REGISTRY,
)
HUMAN_REVIEW_ROUTED = Counter(
    "docflow_human_review_routed_total",
    "Documents routed to human review",
    registry=_REGISTRY,
)
ERP_SUBMISSIONS = Counter(
    "docflow_erp_submissions_total",
    "ERP submission attempts by outcome",
    ["outcome"],
    registry=_REGISTRY,
)
ERP_SUBMISSION_DURATION = Histogram(
    "docflow_erp_submission_duration_seconds",
    "ERP invoice create duration",
    ["outcome"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    registry=_REGISTRY,
)
REVIEW_ACTIONS = Counter(
    "docflow_review_actions_total",
    "Human review commands by action and outcome",
    ["action", "outcome"],
    registry=_REGISTRY,
)

logger = logging.getLogger(__name__)


def registry() -> CollectorRegistry:
    return _REGISTRY


def metrics_payload() -> bytes:
    return generate_latest(_REGISTRY)


def metric_names() -> set[str]:
    return {
        "docflow_documents_ingested_total",
        "docflow_documents_completed_total",
        "docflow_workflow_duration_seconds",
        "docflow_extraction_duration_seconds",
        "docflow_validation_failures_total",
        "docflow_provider_requests_total",
        "docflow_provider_failures_total",
        "docflow_provider_fallback_total",
        "docflow_provider_usage_total",
        "docflow_retries_total",
        "docflow_dlq_depth",
        "docflow_auto_approvals_total",
        "docflow_human_review_routed_total",
        "docflow_erp_submissions_total",
        "docflow_erp_submission_duration_seconds",
        "docflow_review_actions_total",
    }


def safe_inc(counter: Counter, **labels: str) -> None:
    try:
        if labels:
            counter.labels(**labels).inc()
        else:
            counter.inc()
    except Exception:
        logger.debug("metric_inc_failed", exc_info=False)


def safe_observe(histogram: Histogram, value: float, **labels: str) -> None:
    try:
        if labels:
            histogram.labels(**labels).observe(value)
        else:
            histogram.observe(value)
    except Exception:
        logger.debug("metric_observe_failed", exc_info=False)


def set_dlq_depth(depth: int) -> None:
    return None


def refresh_dlq_depth(redis_url: str) -> None:
    configure_dlq_collector(redis_url)


def record_document_ingested(*, service: str = "ingestion") -> None:
    safe_inc(DOCUMENTS_INGESTED, service=service)


def record_workflow_outcome(
    *,
    final_state: str,
    duration_seconds: float | None = None,
) -> None:
    state = bounded_final_state(final_state)
    safe_inc(DOCUMENTS_COMPLETED, final_state=state)
    if duration_seconds is not None and duration_seconds >= 0:
        safe_observe(WORKFLOW_DURATION, duration_seconds, final_state=state)


def record_extraction(
    *,
    provider: str,
    model_name: str | None,
    latency_ms: float | None,
    outcome: str,
    error_class: str | None = None,
) -> None:
    provider_value = bounded_provider(provider)
    family = model_family(provider_value, model_name)
    safe_inc(PROVIDER_REQUESTS, provider=provider_value, outcome=outcome)
    if outcome == "success":
        safe_inc(PROVIDER_USAGE, provider=provider_value, model_family=family)
        if latency_ms is not None and latency_ms >= 0:
            safe_observe(
                EXTRACTION_DURATION,
                latency_ms / 1000.0,
                provider=provider_value,
                model_family=family,
            )
        return
    safe_inc(
        PROVIDER_FAILURES,
        provider=provider_value,
        error_class=bounded_error_class(error_class),
    )


def record_fallback(*, from_provider: str, to_provider: str) -> None:
    safe_inc(
        PROVIDER_FALLBACKS,
        from_provider=bounded_provider(from_provider),
        to_provider=bounded_provider(to_provider),
    )


def record_validation_failures(issue_codes: list[str]) -> None:
    for code in issue_codes:
        safe_inc(
            VALIDATION_FAILURES,
            category=bounded_validation_category(code),
        )


def record_retry(*, operation: str, error_class: str) -> None:
    op = operation if operation in {"process_document", "erp_submit"} else "other"
    safe_inc(
        RETRIES,
        operation=op,
        error_class=bounded_error_class(error_class),
    )


def record_auto_approval() -> None:
    safe_inc(AUTO_APPROVALS)


def record_human_review_routed() -> None:
    safe_inc(HUMAN_REVIEW_ROUTED)


def record_erp_submission(*, outcome: str, duration_seconds: float | None) -> None:
    result = outcome if outcome in {"success", "failure"} else "failure"
    safe_inc(ERP_SUBMISSIONS, outcome=result)
    if duration_seconds is not None and duration_seconds >= 0:
        safe_observe(
            ERP_SUBMISSION_DURATION,
            duration_seconds,
            outcome=result,
        )


def record_review_action(*, action: str, outcome: str) -> None:
    action_value = action if action in {"approve", "reject"} else "other"
    allowed = {"accepted", "rejected", "unchanged", "conflict"}
    outcome_value = outcome if outcome in allowed else "other"
    safe_inc(REVIEW_ACTIONS, action=action_value, outcome=outcome_value)


def parse_counter_value(
    payload: bytes,
    metric_name: str,
    labels: dict[str, str],
) -> float:
    text = payload.decode("utf-8")
    if labels:
        label_parts = ",".join(
            f'{key}="{value}"' for key, value in sorted(labels.items())
        )
        needle = f"{metric_name}{{{label_parts}}}"
        for line in text.splitlines():
            if line.startswith(needle + " ") or line.startswith(needle + "\t"):
                return float(line.split()[-1])
        return 0.0
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        if line.startswith(metric_name + "{"):
            continue
        if line.startswith(metric_name + " ") or line.startswith(metric_name + "\t"):
            return float(line.split()[-1])
    return 0.0


def event_payload(
    *,
    service: str,
    event: str,
    level: str = "info",
    document_id: str | None = None,
    correlation_id: str | None = None,
    workflow_state: str | None = None,
    provider: str | None = None,
    model_id: str | None = None,
    retry_count: int | None = None,
    error_class: str | None = None,
    operation: str | None = None,
    outcome: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "service": service,
        "level": level,
        "event": event,
    }
    if document_id is not None:
        payload["document_id"] = document_id
    if correlation_id is not None:
        payload["correlation_id"] = correlation_id
    if workflow_state is not None:
        payload["workflow_state"] = workflow_state
    if provider is not None:
        payload["provider"] = bounded_provider(provider)
    if model_id is not None:
        payload["model_id"] = model_family(provider, model_id)
    if retry_count is not None:
        payload["retry_count"] = retry_count
    if error_class is not None:
        payload["error_class"] = bounded_error_class(error_class)
    if operation is not None:
        payload["operation"] = operation
    if outcome is not None:
        payload["outcome"] = outcome
    return payload
