from copy import deepcopy

from services.worker.extraction.schemas import parse_invoice_payload
from services.worker.providers.contract import ExtractionRequest, ExtractionResult
from services.worker.providers.errors import (
    ProviderErrorClass,
    ProviderOutputError,
    RecoverableProviderError,
)
from services.worker.providers.fake import FakeLLMProvider, FakeScenario


class DegradedFakeLLMProvider(FakeLLMProvider):
    MODEL_NAME = "fake-degraded-v1"

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        scenario = self._resolve_scenario(request.document_text)
        if scenario is FakeScenario.PROVIDER_FAILURE:
            raise RecoverableProviderError(
                "simulated provider timeout",
                error_class=ProviderErrorClass.TIMEOUT,
                provider=self.PROVIDER_NAME,
            )
        if scenario in {
            FakeScenario.MISSING_FIELDS,
            FakeScenario.MALFORMED,
        }:
            return super().extract(request)
        try:
            result = super().extract(request)
        except ProviderOutputError:
            raise
        degraded = deepcopy(result.invoice.model_dump(mode="json"))
        degraded["invoice_number"] = "INV-DEGRADED-WRONG"
        degraded["total_amount"] = "1.00"
        degraded["supplier_name"] = "Wrong Supplier Ltd"
        invoice = parse_invoice_payload(degraded)
        return ExtractionResult(
            invoice=invoice,
            provider=self.PROVIDER_NAME,
            model_name=self.MODEL_NAME,
            model_version=self._model_version,
            prompt_version=f"{self._prompt_version}-degraded",
            generation_parameters=request.generation_parameters,
            extracted_at=self._extracted_at,
            correlation_id=request.correlation_id,
            document_id=request.document_id,
            latency_ms=self._latency_ms,
            token_usage=self._token_usage,
            fallback_reason="degraded_fixture",
            application_version=self._application_version,
        )


def build_eval_provider(
    *,
    fixture: str,
    prompt_version: str,
) -> FakeLLMProvider:
    if fixture == "degraded":
        return DegradedFakeLLMProvider(
            prompt_version=prompt_version,
            latency_ms=2.0,
            application_version="0.1.0",
        )
    if fixture != "baseline":
        raise ValueError(f"Unsupported evaluation fixture: {fixture}")
    return FakeLLMProvider(
        prompt_version=prompt_version,
        latency_ms=1.0,
        application_version="0.1.0",
    )
