import logging

from services.observability import metrics as obs_metrics
from services.observability.logging import log_event
from services.settings import Settings
from services.worker.providers.contract import (
    ExtractionRequest,
    ExtractionResult,
    LLMProvider,
)
from services.worker.providers.errors import (
    NonRecoverableProviderError,
    ProviderErrorClass,
    RecoverableProviderError,
)
from services.worker.providers.fake import FakeLLMProvider
from services.worker.providers.gemini import GeminiProvider
from services.worker.providers.qwen import LocalQwenProvider

logger = logging.getLogger(__name__)


class ProviderRouter:
    def __init__(
        self,
        *,
        settings: Settings,
        primary: LLMProvider,
        fallback: LLMProvider | None = None,
    ) -> None:
        self._settings = settings
        self._primary = primary
        self._fallback = fallback

    def extract(self, request: ExtractionRequest) -> ExtractionResult:
        try:
            return self._primary.extract(request)
        except RecoverableProviderError as primary_error:
            if not self._settings.llm_fallback_enabled or self._fallback is None:
                raise
            log_event(
                logger,
                service="worker",
                event="provider_fallback_triggered",
                provider=primary_error.provider,
                error_class=primary_error.error_class.value,
                operation="extract",
                outcome="fallback",
            )
            fallback_name = getattr(self._fallback, "PROVIDER_NAME", "unknown")
            obs_metrics.record_fallback(
                from_provider=primary_error.provider,
                to_provider=str(fallback_name),
            )
            result = self._fallback.extract(request)
            return result.model_copy(
                update={
                    "fallback_reason": (
                        f"{primary_error.provider}:{primary_error.error_class.value}"
                    ),
                },
            )


def _build_qwen(settings: Settings) -> LocalQwenProvider:
    return LocalQwenProvider(
        base_url=settings.qwen_base_url,
        model_id=settings.qwen_model_id,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )


def _build_gemini(settings: Settings) -> GeminiProvider:
    return GeminiProvider(
        api_key=settings.gemini_api_key,
        model_id=settings.gemini_model_id,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )


def build_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_mode == "test":
        return FakeLLMProvider()
    if settings.llm_mode == "local":
        return _build_qwen(settings)
    if settings.llm_mode == "gemini_preferred":
        fallback: LLMProvider | None = None
        if settings.llm_fallback_enabled:
            fallback = _build_qwen(settings)
        return ProviderRouter(
            settings=settings,
            primary=_build_gemini(settings),
            fallback=fallback,
        )
    if settings.llm_mode == "qwen_preferred":
        fallback_provider: LLMProvider | None = None
        if settings.llm_fallback_enabled:
            fallback_provider = _build_gemini(settings)
        return ProviderRouter(
            settings=settings,
            primary=_build_qwen(settings),
            fallback=fallback_provider,
        )
    raise NonRecoverableProviderError(
        f"Unsupported LLM_MODE={settings.llm_mode}",
        error_class=ProviderErrorClass.INVALID_REQUEST,
        provider="router",
    )
