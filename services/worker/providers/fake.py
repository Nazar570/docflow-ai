import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from services.worker.extraction.prompt_versions import PROMPT_VERSION
from services.worker.extraction.schemas import parse_invoice_payload
from services.worker.providers.contract import ExtractionRequest, ExtractionResult
from services.worker.providers.errors import (
    ProviderErrorClass,
    ProviderOutputError,
    RecoverableProviderError,
)


class FakeScenario(StrEnum):
    SUCCESS = "success"
    LOW_CONFIDENCE = "low_confidence"
    MISSING_FIELDS = "missing_fields"
    MALFORMED = "malformed"
    UNSUPPORTED_CURRENCY = "unsupported_currency"
    INVALID_TOTAL = "invalid_total"
    PROVIDER_FAILURE = "provider_failure"


class FakeLLMProvider:
    PROVIDER_NAME = "fake"
    MODEL_NAME = "fake-deterministic-v1"

    def __init__(
        self,
        *,
        default_scenario: FakeScenario = FakeScenario.SUCCESS,
        scenarios_by_hash: dict[str, FakeScenario] | None = None,
        prompt_version: str = PROMPT_VERSION,
        latency_ms: float = 1.0,
        extracted_at: datetime | None = None,
        model_version: str | None = "1",
        token_usage: int | None = 128,
        application_version: str | None = None,
    ) -> None:
        self._default_scenario = default_scenario
        self._scenarios_by_hash = scenarios_by_hash or {}
        self._prompt_version = prompt_version
        self._latency_ms = latency_ms
        self._extracted_at = extracted_at or datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        self._model_version = model_version
        self._token_usage = token_usage
        self._application_version = application_version

    @staticmethod
    def text_hash(document_text: str) -> str:
        return hashlib.sha256(document_text.encode("utf-8")).hexdigest()

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        scenario = self._resolve_scenario(request.document_text)
        if scenario is FakeScenario.PROVIDER_FAILURE:
            raise RecoverableProviderError(
                "simulated provider timeout",
                error_class=ProviderErrorClass.TIMEOUT,
                provider=self.PROVIDER_NAME,
            )
        raw_payload = self._raw_payload_for_scenario(
            scenario,
            document_text=request.document_text,
        )
        try:
            invoice = parse_invoice_payload(raw_payload)
        except ValidationError as exc:
            raise ProviderOutputError(
                "Fake provider produced invalid extraction for scenario="
                f"{scenario.value}",
            ) from exc
        return ExtractionResult(
            invoice=invoice,
            provider=self.PROVIDER_NAME,
            model_name=self.MODEL_NAME,
            model_version=self._model_version,
            prompt_version=self._prompt_version,
            generation_parameters=request.generation_parameters,
            extracted_at=self._extracted_at,
            correlation_id=request.correlation_id,
            document_id=request.document_id,
            latency_ms=self._latency_ms,
            token_usage=self._token_usage,
            fallback_reason=None,
            application_version=self._application_version,
        )

    def _resolve_scenario(self, document_text: str) -> FakeScenario:
        marker_prefix = "DOC FLOW SCENARIO:"
        for scenario in FakeScenario:
            marker = f"{marker_prefix}{scenario.value.upper()}"
            if marker in document_text:
                return scenario
        text_hash = self.text_hash(document_text)
        return self._scenarios_by_hash.get(text_hash, self._default_scenario)

    @staticmethod
    def invoice_number_from_text(document_text: str) -> str:
        marker = "INVOICE_NUMBER:"
        for token in document_text.replace("\n", " ").split():
            if token.startswith(marker):
                value = token[len(marker) :].strip(",.;")
                if value:
                    return value
        return "INV-1001"

    def _raw_payload_for_scenario(
        self,
        scenario: FakeScenario,
        *,
        document_text: str,
    ) -> dict[str, Any]:
        invoice_number = self.invoice_number_from_text(document_text)
        if scenario is FakeScenario.SUCCESS:
            return self._success_payload(
                overall_confidence=0.96,
                invoice_number=invoice_number,
            )
        if scenario is FakeScenario.LOW_CONFIDENCE:
            return self._success_payload(
                overall_confidence=0.42,
                invoice_number=invoice_number,
            )
        if scenario is FakeScenario.UNSUPPORTED_CURRENCY:
            payload = self._success_payload(
                overall_confidence=0.96,
                invoice_number=invoice_number,
            )
            payload["currency"] = "GBP"
            return payload
        if scenario is FakeScenario.INVALID_TOTAL:
            payload = self._success_payload(
                overall_confidence=0.96,
                invoice_number=invoice_number,
            )
            payload["total_amount"] = "999.99"
            return payload
        if scenario is FakeScenario.MISSING_FIELDS:
            return {
                "supplier_name": "Acme Supplies GmbH",
                "currency": "EUR",
                "total_amount": "100.00",
                "overall_confidence": 0.91,
            }
        if scenario is FakeScenario.MALFORMED:
            return {
                "supplier_name": "Acme Supplies GmbH",
                "invoice_number": "INV-1001",
                "invoice_date": "2026-03-15",
                "currency": "EUR",
                "total_amount": "not-a-number",
                "line_items": [],
                "overall_confidence": 0.95,
            }
        raise ProviderOutputError(f"Unsupported fake scenario: {scenario}")

    def _success_payload(
        self,
        *,
        overall_confidence: float,
        invoice_number: str = "INV-1001",
    ) -> dict[str, Any]:
        return {
            "supplier_name": "Acme Supplies GmbH",
            "supplier_id": "sup-acme-001",
            "purchase_order_id": "po-1001",
            "invoice_number": invoice_number,
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
                {
                    "description": "Toner cartridge",
                    "quantity": "1",
                    "unit_price": "75.50",
                    "line_total": "75.50",
                },
            ],
            "overall_confidence": overall_confidence,
            "field_confidence": {
                "supplier_name": overall_confidence,
                "invoice_number": overall_confidence,
                "invoice_date": overall_confidence,
                "currency": overall_confidence,
                "total_amount": overall_confidence,
                "line_items": overall_confidence,
            },
        }
