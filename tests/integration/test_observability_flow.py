from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient

from services.observability.metrics import (
    configure_dlq_collector,
    metrics_payload,
    parse_counter_value,
)
from services.settings import Settings
from services.worker.dlq import DeadLetterQueue
from services.worker.extraction.pdf_samples import build_pdf_bytes
from services.worker.providers.contract import ExtractionRequest, ExtractionResult
from services.worker.providers.errors import (
    ProviderErrorClass,
    RecoverableProviderError,
)
from services.worker.providers.fake import FakeLLMProvider
from services.worker.providers.router import ProviderRouter

pytestmark = pytest.mark.integration


def test_metrics_endpoint_after_successful_document(
    ingestion_client: TestClient,
) -> None:
    before = metrics_payload()
    before_ingested = parse_counter_value(
        before,
        "docflow_documents_ingested_total",
        {"service": "ingestion"},
    )
    pdf_bytes = build_pdf_bytes(
        "Fictional invoice for Acme Supplies GmbH INV-OBS-1 EUR 125.50",
    )
    response = ingestion_client.post(
        "/documents",
        data={"source_message_id": "msg-obs-success"},
        files={"file": ("invoice.pdf", pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 202
    body = response.json()
    status = ingestion_client.get(f"/documents/{body['document_id']}").json()
    assert status["status"] in {"submitted", "needs_review", "failed", "queued"}
    metrics = ingestion_client.get("/metrics")
    assert metrics.status_code == 200
    text = metrics.text
    assert "docflow_documents_ingested_total" in text
    assert "docflow_documents_completed_total" in text
    after_ingested = parse_counter_value(
        metrics.content,
        "docflow_documents_ingested_total",
        {"service": "ingestion"},
    )
    assert after_ingested >= before_ingested + 1


def test_needs_review_increments_review_metric(
    ingestion_client: TestClient,
) -> None:
    before = parse_counter_value(
        metrics_payload(),
        "docflow_human_review_routed_total",
        {},
    )
    pdf_bytes = build_pdf_bytes(
        "DOC FLOW SCENARIO:LOW_CONFIDENCE Fictional invoice "
        "Acme Supplies GmbH INV-OBS-REVIEW EUR 125.50",
    )
    response = ingestion_client.post(
        "/documents",
        data={"source_message_id": "msg-obs-review"},
        files={"file": ("invoice.pdf", pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 202
    document_id = response.json()["document_id"]
    status = ingestion_client.get(f"/documents/{document_id}").json()
    assert status["status"] == "needs_review"
    after = parse_counter_value(
        metrics_payload(),
        "docflow_human_review_routed_total",
        {},
    )
    assert after >= before + 1


def test_provider_failure_and_fallback_metrics(settings: Settings) -> None:
    class FailingPrimary:
        PROVIDER_NAME = "gemini"

        def extract(self, request: ExtractionRequest) -> ExtractionResult:
            raise RecoverableProviderError(
                "simulated rate limit",
                error_class=ProviderErrorClass.RATE_LIMIT,
                provider="gemini",
            )

    before_fail = parse_counter_value(
        metrics_payload(),
        "docflow_provider_failures_total",
        {"provider": "gemini", "error_class": "rate_limit"},
    )
    before_fallback = parse_counter_value(
        metrics_payload(),
        "docflow_provider_fallback_total",
        {"from_provider": "gemini", "to_provider": "fake"},
    )
    settings_obj = settings.model_copy(
        update={"llm_fallback_enabled": True, "llm_mode": "gemini_preferred"},
    )
    router = ProviderRouter(
        settings=settings_obj,
        primary=FailingPrimary(),
        fallback=FakeLLMProvider(),
    )
    result = router.extract(
        ExtractionRequest(
            document_text="Fictional invoice Acme Supplies GmbH INV-1 EUR 10.00",
            correlation_id=uuid4(),
            document_id=uuid4(),
        ),
    )
    assert result.fallback_reason is not None
    after_fallback = parse_counter_value(
        metrics_payload(),
        "docflow_provider_fallback_total",
        {"from_provider": "gemini", "to_provider": "fake"},
    )
    assert after_fallback >= before_fallback + 1
    assert before_fail >= 0


def test_dlq_depth_metric_changes(
    settings: Settings,
    cleared_dlq: None,
) -> None:
    configure_dlq_collector(settings.redis_url)
    dlq = DeadLetterQueue(redis_url=settings.redis_url)
    try:
        before = parse_counter_value(metrics_payload(), "docflow_dlq_depth", {})
        dlq.push(
            document_id=uuid4(),
            correlation_id=uuid4(),
            retry_count=3,
            error_class="timeout",
            error_message="exhausted",
        )
        after = parse_counter_value(metrics_payload(), "docflow_dlq_depth", {})
        assert after >= before + 1
    finally:
        dlq.close()


def test_observability_config_files_parse() -> None:
    root = Path("observability")
    prometheus = yaml.safe_load(
        (root / "prometheus" / "prometheus.yml").read_text(encoding="utf-8"),
    )
    alerts = yaml.safe_load(
        (root / "prometheus" / "alerts.yml").read_text(encoding="utf-8"),
    )
    alertmanager = yaml.safe_load(
        (root / "alertmanager" / "alertmanager.yml").read_text(encoding="utf-8"),
    )
    otel = yaml.safe_load(
        (root / "otel-collector" / "config.yaml").read_text(encoding="utf-8"),
    )
    assert "scrape_configs" in prometheus
    assert alerts["groups"][0]["rules"]
    assert alertmanager["receivers"][0]["name"] == "local"
    assert "otlp" in otel["receivers"]
    runbook = Path("docs/runbook.md").read_text(encoding="utf-8")
    for rule in alerts["groups"][0]["rules"]:
        assert rule["alert"] in runbook
    dashboards = list((root / "grafana" / "dashboards").glob("*.json"))
    assert len(dashboards) >= 2
