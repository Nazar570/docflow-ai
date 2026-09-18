from __future__ import annotations

from scripts.demo_common import (
    DEFAULT_ERP,
    DEFAULT_INGESTION,
    DEFAULT_REVIEW_UI,
    erp_invoice_count,
    new_run_id,
    safe_print,
    scenario_pdf_bytes,
    upload_document,
    wait_for_status,
    wait_healthy,
)


def main() -> int:
    run_id = new_run_id()
    base = DEFAULT_INGESTION
    erp = DEFAULT_ERP
    wait_healthy(f"{base}/health", timeout_seconds=60.0)
    wait_healthy(f"{erp}/health", timeout_seconds=60.0)
    wait_healthy(f"{DEFAULT_REVIEW_UI}/_stcore/health", timeout_seconds=60.0)
    before = erp_invoice_count(erp)
    source_message_id = f"smoke-{run_id}"
    uploaded = upload_document(
        base_url=base,
        source_message_id=source_message_id,
        pdf_bytes=scenario_pdf_bytes("SUCCESS", source_message_id),
    )
    status = wait_for_status(
        base,
        str(uploaded["document_id"]),
        wanted={"submitted"},
        timeout_seconds=60.0,
    )
    after = erp_invoice_count(erp)
    if after != before + 1:
        raise RuntimeError("smoke expected exactly one new ERP invoice")
    safe_print(
        "smoke_ok "
        f"document_id={status['document_id']} "
        f"correlation_id={status['correlation_id']} "
        f"status={status['status']} "
        f"erp_delta=1",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
