import os

os.environ.setdefault("OTEL_TRACES_ENABLED", "false")

from services.observability.labels import (
    PROHIBITED_LABEL_KEYS,
    assert_safe_metric_labels,
    bounded_error_class,
    bounded_provider,
    model_family,
)
from services.observability.logging import build_log_record, redact_value
from services.observability.metrics import (
    configure_dlq_collector,
    metric_names,
    metrics_payload,
    parse_counter_value,
    record_document_ingested,
    record_extraction,
    record_fallback,
    record_human_review_routed,
    record_retry,
    record_workflow_outcome,
    set_dlq_depth,
)
from services.observability.tracing import safe_span_attributes, start_span


def test_start_span_propagates_body_exceptions() -> None:
    try:
        with start_span("unit-test-span"):
            raise RuntimeError("boom")
    except RuntimeError as exc:
        assert str(exc) == "boom"
    else:
        raise AssertionError("expected RuntimeError to propagate")


def test_prohibited_labels_rejected() -> None:
    for key in PROHIBITED_LABEL_KEYS:
        try:
            assert_safe_metric_labels({key: "x"})
            raise AssertionError(f"expected rejection for {key}")
        except ValueError:
            pass


def test_bounded_label_helpers() -> None:
    assert bounded_provider("fake") == "fake"
    assert bounded_provider("weird") == "unknown"
    assert bounded_error_class("timeout") == "timeout"
    assert bounded_error_class("boom") == "unknown"
    assert model_family("qwen", "Qwen3-4B") == "qwen"


def test_retry_counting_is_additive_not_deduped_by_document() -> None:
    before = parse_counter_value(
        metrics_payload(),
        "docflow_retries_total",
        {"operation": "process_document", "error_class": "timeout"},
    )
    record_retry(operation="process_document", error_class="timeout")
    record_retry(operation="process_document", error_class="timeout")
    after = parse_counter_value(
        metrics_payload(),
        "docflow_retries_total",
        {"operation": "process_document", "error_class": "timeout"},
    )
    assert after - before == 2.0


def test_workflow_outcome_counts_each_completion() -> None:
    before = parse_counter_value(
        metrics_payload(),
        "docflow_documents_completed_total",
        {"final_state": "submitted"},
    )
    record_workflow_outcome(final_state="submitted", duration_seconds=0.2)
    record_workflow_outcome(final_state="submitted", duration_seconds=0.3)
    after = parse_counter_value(
        metrics_payload(),
        "docflow_documents_completed_total",
        {"final_state": "submitted"},
    )
    assert after - before == 2.0


def test_log_redaction_removes_sensitive_fields() -> None:
    record = build_log_record(
        service="worker",
        event="test_event",
        document_id="d1",
        correlation_id="c1",
        prompt="secret-prompt",
        api_key="secret",
    )
    assert record["document_id"] == "d1"
    assert record["correlation_id"] == "c1"
    assert record["prompt"] == "[redacted]"
    assert record["api_key"] == "[redacted]"
    assert redact_value("content", "pdf-bytes") == "[redacted]"


def test_span_attributes_are_safe() -> None:
    attrs = safe_span_attributes(
        document_id="d1",
        correlation_id="c1",
        provider="fake",
        error_class="timeout",
    )
    assert "document_id" in attrs
    assert "correlation_id" in attrs
    assert "prompt" not in attrs


def test_metric_catalog_stable() -> None:
    names = metric_names()
    assert "docflow_documents_ingested_total" in names
    assert "docflow_dlq_depth" in names
    record_document_ingested()
    record_extraction(
        provider="fake",
        model_name="fake-deterministic-v1",
        latency_ms=1.0,
        outcome="success",
    )
    record_fallback(from_provider="gemini", to_provider="qwen")
    record_human_review_routed()
    set_dlq_depth(2)
    configure_dlq_collector("redis://localhost:6379/0")
    payload = metrics_payload().decode("utf-8")
    assert "docflow_documents_ingested_total" in payload
    assert "docflow_dlq_depth" in payload
