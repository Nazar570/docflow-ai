import os

import pytest

from services.settings import Settings
from services.worker.providers.contract import ExtractionRequest
from services.worker.providers.qwen import LocalQwenProvider

pytestmark = [
    pytest.mark.live_qwen,
    pytest.mark.skipif(
        os.environ.get("RUN_LIVE_QWEN") != "1",
        reason="Set RUN_LIVE_QWEN=1 to enable live llama.cpp checks",
    ),
]


def test_live_qwen_extracts_structured_invoice() -> None:
    settings = Settings(
        app_env="development",
        database_url="postgresql+psycopg://docflow:docflow@localhost:5433/docflow",
        erp_base_url="http://localhost:8001",
        llm_mode="local",
        llm_primary_provider="qwen",
        qwen_base_url=os.environ.get("QWEN_BASE_URL", "http://127.0.0.1:8080/v1"),
        qwen_model_id=os.environ.get(
            "QWEN_MODEL_ID",
            "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
        ),
        llm_timeout_seconds=float(os.environ.get("LLM_TIMEOUT_SECONDS", "60")),
        llm_max_retries=int(os.environ.get("LLM_MAX_RETRIES", "1")),
    )
    provider = LocalQwenProvider(
        base_url=settings.qwen_base_url,
        model_id=settings.qwen_model_id,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )
    try:
        result = provider.extract(
            ExtractionRequest(
                document_text=(
                    "Invoice INV-9001 from Acme Supplies GmbH dated 2026-03-15. "
                    "Currency EUR. Total 125.50. Supplier id sup-acme-001. "
                    "Purchase order po-1001. Line 1 Paper reams qty 5 unit 10.00 "
                    "line 50.00. Line 2 Toner cartridge qty 1 unit 75.50 line 75.50."
                ),
            ),
        )
    finally:
        provider.close()
    assert result.provider == "qwen"
    assert result.invoice.currency in {"EUR", "USD", "PLN"}
    assert result.invoice.total_amount >= 0
