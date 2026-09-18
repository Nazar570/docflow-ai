from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from services.settings import load_settings
from services.worker.extraction.prompt_versions import PROMPT_VERSION
from services.worker.providers.contract import ExtractionRequest, GenerationParameters
from services.worker.providers.errors import ProviderOutputError
from services.worker.providers.fake import FakeLLMProvider, FakeScenario


def test_fake_provider_returns_valid_success_extraction() -> None:
    provider = FakeLLMProvider()
    request = ExtractionRequest(
        document_text="Invoice INV-1001 from Acme Supplies GmbH for EUR 125.50",
        correlation_id=UUID("11111111-1111-1111-1111-111111111111"),
        document_id=UUID("22222222-2222-2222-2222-222222222222"),
        generation_parameters=GenerationParameters(temperature=0.0, max_tokens=1024),
    )
    result = provider.extract(request)
    assert result.provider == "fake"
    assert result.model_name == "fake-deterministic-v1"
    assert result.prompt_version == PROMPT_VERSION
    assert result.correlation_id == request.correlation_id
    assert result.document_id == request.document_id
    assert result.fallback_reason is None
    assert result.invoice.total_amount == Decimal("125.50")
    assert result.invoice.overall_confidence == 0.96
    assert result.invoice.field_confidence.total_amount == 0.96


def test_fake_provider_is_deterministic_for_identical_inputs() -> None:
    provider = FakeLLMProvider(
        extracted_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC),
        latency_ms=2.5,
    )
    request = ExtractionRequest(document_text="Deterministic invoice text")
    first = provider.extract(request)
    second = provider.extract(request)
    assert first.model_dump() == second.model_dump()


def test_fake_provider_low_confidence_scenario() -> None:
    document_text = "Low confidence invoice text"
    text_hash = FakeLLMProvider.text_hash(document_text)
    provider = FakeLLMProvider(
        scenarios_by_hash={text_hash: FakeScenario.LOW_CONFIDENCE},
    )
    result = provider.extract(ExtractionRequest(document_text=document_text))
    assert result.invoice.overall_confidence == 0.42
    assert result.invoice.invoice_number == "INV-1001"


def test_fake_provider_missing_fields_raises_provider_output_error() -> None:
    provider = FakeLLMProvider(default_scenario=FakeScenario.MISSING_FIELDS)
    with pytest.raises(ProviderOutputError):
        provider.extract(ExtractionRequest(document_text="Missing fields invoice"))


def test_fake_provider_malformed_payload_raises_provider_output_error() -> None:
    provider = FakeLLMProvider(default_scenario=FakeScenario.MALFORMED)
    with pytest.raises(ProviderOutputError):
        provider.extract(ExtractionRequest(document_text="Malformed invoice"))


def test_settings_default_llm_mode_is_test(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_MODE", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://docflow:docflow@localhost:5433/docflow",
    )
    monkeypatch.setenv("ERP_BASE_URL", "http://localhost:8001")
    settings = load_settings()
    assert settings.llm_mode == "test"
    assert settings.app_env == "development"


def test_settings_rejects_unsupported_llm_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_MODE", "unsupported")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://docflow:docflow@localhost:5433/docflow",
    )
    monkeypatch.setenv("ERP_BASE_URL", "http://localhost:8001")
    with pytest.raises(ValueError):
        load_settings()


def test_fake_provider_marker_selects_low_confidence() -> None:
    provider = FakeLLMProvider()
    result = provider.extract(
        ExtractionRequest(
            document_text="DOC FLOW SCENARIO:LOW_CONFIDENCE invoice body",
        ),
    )
    assert result.invoice.overall_confidence == 0.42


def test_fake_provider_optional_invoice_number_marker() -> None:
    provider = FakeLLMProvider()
    result = provider.extract(
        ExtractionRequest(
            document_text=(
                "DOC FLOW SCENARIO:SUCCESS INVOICE_NUMBER:INV-DEMO-42 body"
            ),
        ),
    )
    assert result.invoice.invoice_number == "INV-DEMO-42"
