from typing import Any

PROHIBITED_LABEL_KEYS = frozenset(
    {
        "document_id",
        "correlation_id",
        "invoice_number",
        "supplier_name",
        "actor",
        "reviewer",
        "user",
        "prompt",
        "raw_path",
        "exception_message",
        "stack_trace",
    },
)

ALLOWED_PROVIDERS = frozenset(
    {"fake", "qwen", "gemini", "human_review", "router", "unknown"},
)

ALLOWED_FINAL_STATES = frozenset(
    {"submitted", "needs_review", "rejected", "failed"},
)

ALLOWED_ERROR_CLASSES = frozenset(
    {
        "rate_limit",
        "quota_exhausted",
        "timeout",
        "transient",
        "auth",
        "invalid_request",
        "output_invalid",
        "unavailable",
        "provider_output_error",
        "pdf_extraction_error",
        "erp_submission_error",
        "erp_transient",
        "missing_supplier_id",
        "unexpected_error",
        "unknown",
    },
)

ALLOWED_VALIDATION_CATEGORIES = frozenset(
    {
        "negative_total",
        "negative_line_amount",
        "unsupported_currency",
        "total_mismatch",
        "low_confidence",
        "auto_approval_limit_exceeded",
        "unknown_supplier",
        "unknown_purchase_order",
        "duplicate_invoice",
        "provider_output_error",
        "other",
    },
)

ALLOWED_MODEL_FAMILIES = frozenset(
    {"fake", "qwen", "gemini", "human_review", "other"},
)


def bounded_provider(provider: str | None) -> str:
    value = (provider or "unknown").strip().lower()
    if value in ALLOWED_PROVIDERS:
        return value
    return "unknown"


def bounded_error_class(error_class: str | None) -> str:
    value = (error_class or "unknown").strip().lower()
    if value in ALLOWED_ERROR_CLASSES:
        return value
    return "unknown"


def bounded_final_state(state: str | None) -> str:
    value = (state or "").strip().lower()
    if value in ALLOWED_FINAL_STATES:
        return value
    return "failed"


def bounded_validation_category(code: str | None) -> str:
    value = (code or "other").strip().lower()
    if value in ALLOWED_VALIDATION_CATEGORIES:
        return value
    return "other"


def model_family(provider: str | None, model_name: str | None = None) -> str:
    provider_value = bounded_provider(provider)
    if provider_value in {"fake", "qwen", "gemini", "human_review"}:
        return provider_value
    if model_name:
        lowered = model_name.lower()
        if "qwen" in lowered:
            return "qwen"
        if "gemini" in lowered:
            return "gemini"
        if "fake" in lowered:
            return "fake"
    return "other"


def assert_safe_metric_labels(labels: dict[str, Any]) -> None:
    for key in labels:
        if key in PROHIBITED_LABEL_KEYS:
            raise ValueError(f"Prohibited metric label: {key}")
