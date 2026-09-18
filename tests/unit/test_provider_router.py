from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from services.settings import Settings
from services.worker.extraction.schemas import Invoice
from services.worker.providers.contract import ExtractionRequest, ExtractionResult
from services.worker.providers.errors import (
    NonRecoverableProviderError,
    ProviderErrorClass,
    RecoverableProviderError,
)
from services.worker.providers.fake import FakeLLMProvider
from services.worker.providers.gemini import GeminiProvider
from services.worker.providers.qwen import LocalQwenProvider
from services.worker.providers.router import ProviderRouter, build_llm_provider


def _settings(**overrides: object) -> Settings:
    payload: dict[str, object] = {
        "app_env": "test",
        "database_url": "postgresql+psycopg://docflow:docflow@localhost:5433/docflow",
        "erp_base_url": "http://localhost:8001",
        "llm_mode": "test",
        "llm_primary_provider": "fake",
        "llm_fallback_enabled": False,
        "qwen_base_url": "http://127.0.0.1:8080/v1",
        "qwen_model_id": "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        "gemini_api_key": "test-key",
        "gemini_model_id": "gemini-2.5-flash",
        "llm_timeout_seconds": 5.0,
        "llm_max_retries": 1,
    }
    payload.update(overrides)
    return Settings.model_validate(payload)


def _valid_invoice_json() -> str:
    return (
        '{"supplier_name":"Acme Supplies GmbH","supplier_id":"sup-acme-001",'
        '"purchase_order_id":"po-1001","invoice_number":"INV-1001",'
        '"invoice_date":"2026-03-15","currency":"EUR","total_amount":"125.50",'
        '"line_items":[{"description":"Paper reams","quantity":"5",'
        '"unit_price":"10.00","line_total":"50.00"},'
        '{"description":"Toner cartridge","quantity":"1","unit_price":"75.50",'
        '"line_total":"75.50"}],"overall_confidence":0.96,'
        '"field_confidence":{"supplier_name":0.96,"invoice_number":0.96,'
        '"invoice_date":0.96,"currency":0.96,"total_amount":0.96,"line_items":0.96}}'
    )


class RecordingProvider:
    def __init__(
        self,
        *,
        name: str,
        behavior: list[Exception | ExtractionResult],
    ) -> None:
        self.name = name
        self.calls = 0
        self._behavior = list(behavior)

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        self.calls += 1
        if not self._behavior:
            raise RuntimeError("No behavior left")
        item = self._behavior.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _success_result(*, provider: str) -> ExtractionResult:
    invoice = Invoice.model_validate_json(_valid_invoice_json())
    return ExtractionResult(
        invoice=invoice,
        provider=provider,
        model_name=f"{provider}-model",
        model_version="1",
        prompt_version="invoice_extraction_v1",
        generation_parameters=ExtractionRequest(
            document_text="x",
        ).generation_parameters,
        extracted_at=datetime(2026, 1, 1, tzinfo=UTC),
        latency_ms=1.0,
        token_usage=10,
        fallback_reason=None,
    )


def test_build_provider_matrix_for_modes() -> None:
    assert isinstance(build_llm_provider(_settings(llm_mode="test")), FakeLLMProvider)
    local = build_llm_provider(
        _settings(llm_mode="local", llm_primary_provider="qwen"),
    )
    assert isinstance(local, LocalQwenProvider)
    gemini_router = build_llm_provider(
        _settings(
            llm_mode="gemini_preferred",
            llm_primary_provider="gemini",
            llm_fallback_enabled=True,
        ),
    )
    assert isinstance(gemini_router, ProviderRouter)
    qwen_router = build_llm_provider(
        _settings(
            llm_mode="qwen_preferred",
            llm_primary_provider="qwen",
            llm_fallback_enabled=True,
        ),
    )
    assert isinstance(qwen_router, ProviderRouter)


def test_router_fallbacks_on_recoverable_gemini_errors() -> None:
    settings = _settings(
        llm_mode="gemini_preferred",
        llm_primary_provider="gemini",
        llm_fallback_enabled=True,
    )
    primary = RecordingProvider(
        name="gemini",
        behavior=[
            RecoverableProviderError(
                "rate limited",
                error_class=ProviderErrorClass.RATE_LIMIT,
                provider="gemini",
            ),
        ],
    )
    fallback = RecordingProvider(
        name="qwen",
        behavior=[_success_result(provider="qwen")],
    )
    router = ProviderRouter(settings=settings, primary=primary, fallback=fallback)
    result = router.extract(ExtractionRequest(document_text="invoice text"))
    assert primary.calls == 1
    assert fallback.calls == 1
    assert result.provider == "qwen"
    assert result.fallback_reason == "gemini:rate_limit"


@pytest.mark.parametrize(
    "error_class",
    [
        ProviderErrorClass.RATE_LIMIT,
        ProviderErrorClass.QUOTA_EXHAUSTED,
        ProviderErrorClass.TIMEOUT,
        ProviderErrorClass.TRANSIENT,
    ],
)
def test_router_fallbacks_for_all_recoverable_classes(
    error_class: ProviderErrorClass,
) -> None:
    settings = _settings(
        llm_mode="gemini_preferred",
        llm_primary_provider="gemini",
        llm_fallback_enabled=True,
    )
    primary = RecordingProvider(
        name="gemini",
        behavior=[
            RecoverableProviderError(
                "recoverable",
                error_class=error_class,
                provider="gemini",
            ),
        ],
    )
    fallback = RecordingProvider(
        name="qwen",
        behavior=[_success_result(provider="qwen")],
    )
    router = ProviderRouter(settings=settings, primary=primary, fallback=fallback)
    result = router.extract(ExtractionRequest(document_text="invoice text"))
    assert result.fallback_reason == f"gemini:{error_class.value}"


def test_router_does_not_fallback_on_non_recoverable_error() -> None:
    settings = _settings(
        llm_mode="gemini_preferred",
        llm_primary_provider="gemini",
        llm_fallback_enabled=True,
    )
    primary = RecordingProvider(
        name="gemini",
        behavior=[
            NonRecoverableProviderError(
                "bad auth",
                error_class=ProviderErrorClass.AUTH,
                provider="gemini",
            ),
        ],
    )
    fallback = RecordingProvider(
        name="qwen",
        behavior=[_success_result(provider="qwen")],
    )
    router = ProviderRouter(settings=settings, primary=primary, fallback=fallback)
    with pytest.raises(NonRecoverableProviderError):
        router.extract(ExtractionRequest(document_text="invoice text"))
    assert fallback.calls == 0


def test_router_does_not_fallback_when_disabled() -> None:
    settings = _settings(
        llm_mode="gemini_preferred",
        llm_primary_provider="gemini",
        llm_fallback_enabled=False,
    )
    primary = RecordingProvider(
        name="gemini",
        behavior=[
            RecoverableProviderError(
                "timeout",
                error_class=ProviderErrorClass.TIMEOUT,
                provider="gemini",
            ),
        ],
    )
    fallback = RecordingProvider(
        name="qwen",
        behavior=[_success_result(provider="qwen")],
    )
    router = ProviderRouter(settings=settings, primary=primary, fallback=fallback)
    with pytest.raises(RecoverableProviderError):
        router.extract(ExtractionRequest(document_text="invoice text"))
    assert fallback.calls == 0


def test_qwen_provider_corrects_invalid_json_once() -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            body = {
                "choices": [
                    {"message": {"content": '{"supplier_name":"Acme"}'}},
                ],
                "usage": {"total_tokens": 11},
            }
            return httpx.Response(200, json=body)
        body = {
            "choices": [{"message": {"content": _valid_invoice_json()}}],
            "usage": {"total_tokens": 22},
        }
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="http://qwen.test/v1")
    provider = LocalQwenProvider(
        base_url="http://qwen.test/v1",
        model_id="qwen-test",
        timeout_seconds=5.0,
        max_retries=0,
        http_client=client,
    )
    result = provider.extract(ExtractionRequest(document_text="invoice text"))
    assert calls["count"] == 2
    assert result.invoice.total_amount == Decimal("125.50")
    assert result.provider == "qwen"
    client.close()


def test_gemini_provider_corrects_invalid_json_once() -> None:
    calls = {"count": 0}

    def generate_content(
        model_id: str,
        prompt: str,
        config: dict[str, object],
    ) -> tuple[str, int | None]:
        calls["count"] += 1
        if calls["count"] == 1:
            return '{"supplier_name":"Acme"}', 5
        return _valid_invoice_json(), 15

    provider = GeminiProvider(
        api_key="test-key",
        model_id="gemini-2.5-flash",
        timeout_seconds=5.0,
        max_retries=0,
        generate_content=generate_content,
    )
    result = provider.extract(ExtractionRequest(document_text="invoice text"))
    assert calls["count"] == 2
    assert result.invoice.invoice_number == "INV-1001"
    assert result.provider == "gemini"


def test_qwen_maps_timeout_to_recoverable_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timeout", request=request)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url="http://qwen.test/v1")
    provider = LocalQwenProvider(
        base_url="http://qwen.test/v1",
        model_id="qwen-test",
        timeout_seconds=1.0,
        max_retries=0,
        http_client=client,
    )
    with pytest.raises(RecoverableProviderError) as exc_info:
        provider.extract(ExtractionRequest(document_text="invoice text"))
    assert exc_info.value.error_class is ProviderErrorClass.TIMEOUT
    client.close()


def test_gemini_maps_rate_limit_to_recoverable_error() -> None:
    def generate_content(
        model_id: str,
        prompt: str,
        config: dict[str, object],
    ) -> tuple[str, int | None]:
        raise RecoverableProviderError(
            "Gemini rate limited",
            error_class=ProviderErrorClass.RATE_LIMIT,
            provider="gemini",
        )

    provider = GeminiProvider(
        api_key="test-key",
        model_id="gemini-2.5-flash",
        timeout_seconds=5.0,
        max_retries=0,
        generate_content=generate_content,
    )
    with pytest.raises(RecoverableProviderError) as exc_info:
        provider.extract(ExtractionRequest(document_text="invoice text"))
    assert exc_info.value.error_class is ProviderErrorClass.RATE_LIMIT
