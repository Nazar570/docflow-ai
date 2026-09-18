import logging
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from services.worker.extraction.prompt_versions import PROMPT_VERSION
from services.worker.providers.contract import (
    ExtractionRequest,
    ExtractionResult,
    GenerationParameters,
)
from services.worker.providers.errors import (
    NonRecoverableProviderError,
    ProviderErrorClass,
    ProviderOutputError,
    RecoverableProviderError,
)
from services.worker.providers.extraction_support import (
    build_extraction_messages,
    format_validation_feedback,
    validate_invoice_text,
)
from services.worker.providers.retry import call_with_retries

logger = logging.getLogger(__name__)


class LocalQwenProvider:
    PROVIDER_NAME = "qwen"

    def __init__(
        self,
        *,
        base_url: str,
        model_id: str,
        timeout_seconds: float,
        max_retries: int,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model_id = model_id
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(
            base_url=self._base_url,
            timeout=timeout_seconds,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        started = time.perf_counter()
        messages = build_extraction_messages(request.document_text)
        try:
            raw_text, token_usage = call_with_retries(
                lambda: self._complete(messages, request.generation_parameters),
                max_retries=self._max_retries,
            )
            invoice = validate_invoice_text(raw_text)
        except ProviderOutputError as first_error:
            feedback = format_validation_feedback(first_error)
            correction_messages = build_extraction_messages(
                request.document_text,
                validation_feedback=feedback,
            )
            try:
                raw_text, token_usage = call_with_retries(
                    lambda: self._complete(
                        correction_messages,
                        request.generation_parameters,
                    ),
                    max_retries=self._max_retries,
                )
                invoice = validate_invoice_text(raw_text)
            except ProviderOutputError:
                raise
        latency_ms = (time.perf_counter() - started) * 1000.0
        logger.info(
            "provider_extraction_completed provider=%s model=%s latency_ms=%.2f",
            self.PROVIDER_NAME,
            self._model_id,
            latency_ms,
        )
        return ExtractionResult(
            invoice=invoice,
            provider=self.PROVIDER_NAME,
            model_name=self._model_id,
            model_version=None,
            prompt_version=PROMPT_VERSION,
            generation_parameters=request.generation_parameters,
            extracted_at=datetime.now(UTC),
            correlation_id=request.correlation_id,
            document_id=request.document_id,
            latency_ms=latency_ms,
            token_usage=token_usage,
            fallback_reason=None,
            application_version=None,
        )

    def _complete(
        self,
        messages: list[dict[str, str]],
        generation_parameters: GenerationParameters,
    ) -> tuple[str, int | None]:
        payload: dict[str, Any] = {
            "model": self._model_id,
            "messages": messages,
            "temperature": generation_parameters.temperature,
            "max_tokens": generation_parameters.max_tokens,
            "response_format": {"type": "json_object"},
        }
        try:
            response = self._client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise RecoverableProviderError(
                "Qwen request timed out",
                error_class=ProviderErrorClass.TIMEOUT,
                provider=self.PROVIDER_NAME,
            ) from exc
        except httpx.TransportError as exc:
            raise RecoverableProviderError(
                "Qwen transport error",
                error_class=ProviderErrorClass.TRANSIENT,
                provider=self.PROVIDER_NAME,
            ) from exc
        if response.status_code == 429:
            raise RecoverableProviderError(
                "Qwen rate limited",
                error_class=ProviderErrorClass.RATE_LIMIT,
                provider=self.PROVIDER_NAME,
            )
        if response.status_code in {500, 502, 503, 504}:
            raise RecoverableProviderError(
                "Qwen transient server error",
                error_class=ProviderErrorClass.TRANSIENT,
                provider=self.PROVIDER_NAME,
            )
        if response.status_code in {401, 403}:
            raise NonRecoverableProviderError(
                "Qwen authentication failed",
                error_class=ProviderErrorClass.AUTH,
                provider=self.PROVIDER_NAME,
            )
        if response.status_code >= 400:
            raise NonRecoverableProviderError(
                "Qwen request failed",
                error_class=ProviderErrorClass.INVALID_REQUEST,
                provider=self.PROVIDER_NAME,
            )
        body = response.json()
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderOutputError("Qwen response missing choices")
        message = choices[0].get("message", {})
        content = message.get("content")
        if not isinstance(content, str):
            raise ProviderOutputError("Qwen response missing message content")
        usage = body.get("usage")
        token_usage: int | None = None
        if isinstance(usage, dict):
            total_tokens = usage.get("total_tokens")
            if isinstance(total_tokens, int):
                token_usage = total_tokens
        return content, token_usage
