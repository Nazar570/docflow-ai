import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from services.worker.extraction.prompt_versions import (
    PROMPT_VERSION,
    load_prompt_template,
)
from services.worker.extraction.schemas import Invoice
from services.worker.providers.contract import ExtractionRequest, ExtractionResult
from services.worker.providers.errors import (
    NonRecoverableProviderError,
    ProviderError,
    ProviderErrorClass,
    ProviderOutputError,
    RecoverableProviderError,
)
from services.worker.providers.extraction_support import (
    format_validation_feedback,
    validate_invoice_text,
)
from services.worker.providers.retry import call_with_retries

logger = logging.getLogger(__name__)

GenerateContentFn = Callable[[str, str, dict[str, Any]], tuple[str, int | None]]


class GeminiProvider:
    PROVIDER_NAME = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model_id: str,
        timeout_seconds: float,
        max_retries: int,
        generate_content: GenerateContentFn | None = None,
    ) -> None:
        if not api_key:
            raise NonRecoverableProviderError(
                "GEMINI_API_KEY is required for Gemini provider",
                error_class=ProviderErrorClass.AUTH,
                provider=self.PROVIDER_NAME,
            )
        if not model_id:
            raise NonRecoverableProviderError(
                "GEMINI_MODEL_ID is required for Gemini provider",
                error_class=ProviderErrorClass.INVALID_REQUEST,
                provider=self.PROVIDER_NAME,
            )
        self._api_key = api_key
        self._model_id = model_id
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._generate_content = generate_content or self._generate_with_sdk

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        started = time.perf_counter()
        prompt = self._build_prompt(request.document_text)
        config = self._build_config(request)
        try:
            raw_text, token_usage = call_with_retries(
                lambda: self._generate_content(self._model_id, prompt, config),
                max_retries=self._max_retries,
            )
            invoice = validate_invoice_text(raw_text)
        except ProviderOutputError as first_error:
            feedback = format_validation_feedback(first_error)
            correction_prompt = self._build_prompt(
                request.document_text,
                validation_feedback=feedback,
            )
            try:
                raw_text, token_usage = call_with_retries(
                    lambda: self._generate_content(
                        self._model_id,
                        correction_prompt,
                        config,
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

    def _build_prompt(
        self,
        document_text: str,
        *,
        validation_feedback: str | None = None,
    ) -> str:
        template = load_prompt_template()
        prompt = (
            f"{template}\n\n"
            f"Prompt version: {PROMPT_VERSION}\n"
            "Return only JSON matching the invoice schema.\n"
            f"Document text:\n{document_text}"
        )
        if validation_feedback is not None:
            prompt = (
                f"{prompt}\n\n"
                "Previous JSON failed validation. Return corrected JSON only.\n"
                f"Validation errors:\n{validation_feedback}"
            )
        return prompt

    def _build_config(self, request: ExtractionRequest) -> dict[str, Any]:
        return {
            "temperature": request.generation_parameters.temperature,
            "max_output_tokens": request.generation_parameters.max_tokens,
            "response_mime_type": "application/json",
            "response_schema": Invoice,
            "http_options": {"timeout": int(self._timeout_seconds * 1000)},
        }

    def _generate_with_sdk(
        self,
        model_id: str,
        prompt: str,
        config: dict[str, Any],
    ) -> tuple[str, int | None]:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise NonRecoverableProviderError(
                "google-genai package is not installed",
                error_class=ProviderErrorClass.UNAVAILABLE,
                provider=self.PROVIDER_NAME,
            ) from exc
        client = genai.Client(api_key=self._api_key)
        try:
            response = client.models.generate_content(
                model=model_id,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=config["temperature"],
                    max_output_tokens=config["max_output_tokens"],
                    response_mime_type=config["response_mime_type"],
                    response_schema=config["response_schema"],
                    http_options=types.HttpOptions(
                        timeout=config["http_options"]["timeout"],
                    ),
                ),
            )
        except Exception as exc:
            raise self._classify_sdk_exception(exc) from exc
        text = getattr(response, "text", None)
        if not isinstance(text, str) or not text.strip():
            raise ProviderOutputError("Gemini response missing text content")
        token_usage: int | None = None
        usage = getattr(response, "usage_metadata", None)
        if usage is not None:
            total = getattr(usage, "total_token_count", None)
            if isinstance(total, int):
                token_usage = total
        return text, token_usage

    def _classify_sdk_exception(self, exc: Exception) -> ProviderError:
        message = str(exc).lower()
        if "timeout" in message or "timed out" in message:
            return RecoverableProviderError(
                "Gemini request timed out",
                error_class=ProviderErrorClass.TIMEOUT,
                provider=self.PROVIDER_NAME,
            )
        if "429" in message or "rate" in message:
            return RecoverableProviderError(
                "Gemini rate limited",
                error_class=ProviderErrorClass.RATE_LIMIT,
                provider=self.PROVIDER_NAME,
            )
        if "quota" in message or "resource_exhausted" in message:
            return RecoverableProviderError(
                "Gemini quota exhausted",
                error_class=ProviderErrorClass.QUOTA_EXHAUSTED,
                provider=self.PROVIDER_NAME,
            )
        if "503" in message or "unavailable" in message or "500" in message:
            return RecoverableProviderError(
                "Gemini transient server error",
                error_class=ProviderErrorClass.TRANSIENT,
                provider=self.PROVIDER_NAME,
            )
        if "401" in message or "403" in message or "api key" in message:
            return NonRecoverableProviderError(
                "Gemini authentication failed",
                error_class=ProviderErrorClass.AUTH,
                provider=self.PROVIDER_NAME,
            )
        return NonRecoverableProviderError(
            "Gemini request failed",
            error_class=ProviderErrorClass.INVALID_REQUEST,
            provider=self.PROVIDER_NAME,
        )
