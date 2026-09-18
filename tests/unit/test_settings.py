from decimal import Decimal

import pytest

from services.settings import load_settings


def test_load_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_MODE", raising=False)
    monkeypatch.delenv("LLM_PRIMARY_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_FALLBACK_ENABLED", raising=False)
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://docflow:docflow@localhost:5433/docflow",
    )
    monkeypatch.setenv("ERP_BASE_URL", "http://localhost:8001")
    settings = load_settings()
    assert settings.llm_mode == "test"
    assert settings.llm_primary_provider == "fake"
    assert settings.llm_fallback_enabled is False
    assert settings.auto_approval_limit == Decimal("1000.00")
    assert settings.supported_currencies == ["EUR", "USD", "PLN"]
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.celery_worker_max_retries == 3


def test_load_settings_accepts_local_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODE", "local")
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "qwen")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://docflow:docflow@localhost:5433/docflow",
    )
    monkeypatch.setenv("ERP_BASE_URL", "http://localhost:8001")
    settings = load_settings()
    assert settings.llm_mode == "local"
    assert settings.llm_primary_provider == "qwen"


def test_settings_rejects_mismatched_primary_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_MODE", "local")
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "gemini")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://docflow:docflow@localhost:5433/docflow",
    )
    monkeypatch.setenv("ERP_BASE_URL", "http://localhost:8001")
    with pytest.raises(ValueError):
        load_settings()


def test_settings_rejects_unknown_llm_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_MODE", "cloud_only")
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://docflow:docflow@localhost:5433/docflow",
    )
    monkeypatch.setenv("ERP_BASE_URL", "http://localhost:8001")
    with pytest.raises(ValueError):
        load_settings()
