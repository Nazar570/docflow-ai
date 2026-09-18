import os
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_env: str = Field(default="development", min_length=1)
    database_url: str = Field(min_length=1)
    erp_base_url: str = Field(min_length=1)
    redis_url: str = Field(default="redis://localhost:6379/0", min_length=1)
    llm_mode: Literal["test", "local", "gemini_preferred", "qwen_preferred"] = "test"
    llm_primary_provider: Literal["fake", "qwen", "gemini"] = "fake"
    llm_fallback_enabled: bool = False
    qwen_base_url: str = Field(default="http://127.0.0.1:8080/v1", min_length=1)
    qwen_model_id: str = Field(
        default="Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        min_length=1,
    )
    gemini_api_key: str = ""
    gemini_model_id: str = Field(default="gemini-2.5-flash")
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0)
    auto_approval_confidence_threshold: float = Field(default=0.90, ge=0.0, le=1.0)
    auto_approval_limit: Decimal = Field(default=Decimal("1000.00"), ge=0)
    supported_currencies: list[str] = Field(
        default_factory=lambda: ["EUR", "USD", "PLN"],
    )
    totals_tolerance: Decimal = Field(default=Decimal("0.01"), ge=0)
    document_storage_dir: Path = Field(default=Path("data/documents"))
    max_upload_bytes: int = Field(default=10_000_000, gt=0)
    erp_timeout_seconds: float = Field(default=10.0, gt=0)
    celery_worker_max_retries: int = Field(default=3, ge=0)
    celery_retry_backoff_base: float = Field(default=2.0, gt=1.0)
    celery_retry_backoff_max: float = Field(default=60.0, gt=0)
    review_api_base_url: str = Field(default="http://localhost:8000", min_length=1)

    @field_validator("supported_currencies", mode="before")
    @classmethod
    def split_currencies(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean setting value: {value}")


def _expected_primary(mode: str) -> str:
    if mode == "test":
        return "fake"
    if mode in {"local", "qwen_preferred"}:
        return "qwen"
    if mode == "gemini_preferred":
        return "gemini"
    raise ValueError(f"Unsupported LLM_MODE={mode}")


def load_settings() -> Settings:
    llm_mode_raw = os.environ.get("LLM_MODE", "test")
    if llm_mode_raw not in {"test", "local", "gemini_preferred", "qwen_preferred"}:
        raise ValueError(
            "LLM_MODE must be one of test, local, gemini_preferred, qwen_preferred",
        )
    expected_primary = _expected_primary(llm_mode_raw)
    llm_primary_raw = os.environ.get("LLM_PRIMARY_PROVIDER", expected_primary)
    if llm_primary_raw != expected_primary:
        raise ValueError(
            "LLM_PRIMARY_PROVIDER must match LLM_MODE "
            f"(expected {expected_primary} for mode {llm_mode_raw})",
        )
    database_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://docflow:docflow@localhost:5433/docflow",
    )
    erp_base_url = os.environ.get("ERP_BASE_URL", "http://localhost:8001")
    supported_currencies_raw = os.environ.get("SUPPORTED_CURRENCIES", "EUR,USD,PLN")
    return Settings.model_validate(
        {
            "app_env": os.environ.get("APP_ENV", "development"),
            "database_url": database_url,
            "erp_base_url": erp_base_url,
            "redis_url": os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            "llm_mode": llm_mode_raw,
            "llm_primary_provider": llm_primary_raw,
            "llm_fallback_enabled": _parse_bool(
                os.environ.get("LLM_FALLBACK_ENABLED"),
                default=False,
            ),
            "qwen_base_url": os.environ.get(
                "QWEN_BASE_URL",
                "http://127.0.0.1:8080/v1",
            ),
            "qwen_model_id": os.environ.get(
                "QWEN_MODEL_ID",
                "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
            ),
            "gemini_api_key": os.environ.get("GEMINI_API_KEY", ""),
            "gemini_model_id": os.environ.get(
                "GEMINI_MODEL_ID",
                "gemini-2.5-flash",
            ),
            "llm_timeout_seconds": float(os.environ.get("LLM_TIMEOUT_SECONDS", "60")),
            "llm_max_retries": int(os.environ.get("LLM_MAX_RETRIES", "2")),
            "auto_approval_confidence_threshold": float(
                os.environ.get("AUTO_APPROVAL_CONFIDENCE_THRESHOLD", "0.90"),
            ),
            "auto_approval_limit": Decimal(
                os.environ.get("AUTO_APPROVAL_LIMIT", "1000.00"),
            ),
            "supported_currencies": [
                part.strip()
                for part in supported_currencies_raw.split(",")
                if part.strip()
            ],
            "totals_tolerance": Decimal(os.environ.get("TOTALS_TOLERANCE", "0.01")),
            "document_storage_dir": Path(
                os.environ.get("DOCUMENT_STORAGE_DIR", "data/documents"),
            ),
            "max_upload_bytes": int(os.environ.get("MAX_UPLOAD_BYTES", "10000000")),
            "erp_timeout_seconds": float(os.environ.get("ERP_TIMEOUT_SECONDS", "10")),
            "celery_worker_max_retries": int(
                os.environ.get("CELERY_WORKER_MAX_RETRIES", "3"),
            ),
            "celery_retry_backoff_base": float(
                os.environ.get("CELERY_RETRY_BACKOFF_BASE", "2"),
            ),
            "celery_retry_backoff_max": float(
                os.environ.get("CELERY_RETRY_BACKOFF_MAX", "60"),
            ),
            "review_api_base_url": os.environ.get(
                "REVIEW_API_BASE_URL",
                "http://localhost:8000",
            ),
        },
    )
