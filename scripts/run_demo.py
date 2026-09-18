from __future__ import annotations

import copy
from typing import Any

from scripts.demo_common import (
    DEFAULT_ERP,
    DEFAULT_INGESTION,
    DEFAULT_REVIEW_UI,
    TERMINAL_STATES,
    approve_review,
    erp_invoice_count,
    get_review_detail,
    list_dlq,
    new_run_id,
    reject_review,
    safe_print,
    scenario_pdf_bytes,
    unique_invoice_number,
    upload_document,
    wait_for_status,
    wait_healthy,
    write_seed_pdfs,
)


def _edited_invoice_from_review(detail: dict[str, Any]) -> dict[str, Any]:
    extraction = detail.get("extraction")
    if not isinstance(extraction, dict):
        raise RuntimeError("review detail missing extraction")
    payload = extraction.get("payload")
    if not isinstance(payload, dict):
        raise RuntimeError("review detail missing extraction payload")
    edited = copy.deepcopy(payload)
    edited["overall_confidence"] = 0.96
    edited["invoice_number"] = unique_invoice_number(
        f"approve-{detail.get('document_id')}",
    )
    field_confidence = edited.get("field_confidence")
    if isinstance(field_confidence, dict):
        for key in list(field_confidence.keys()):
            field_confidence[key] = 0.96
    return edited


def main() -> int:
    run_id = new_run_id()
    base = DEFAULT_INGESTION
    erp = DEFAULT_ERP
    write_seed_pdfs()
    wait_healthy(f"{base}/health")
    wait_healthy(f"{erp}/health")
    wait_healthy(f"{DEFAULT_REVIEW_UI}/_stcore/health")
    safe_print(f"demo_run_id={run_id}")
    safe_print("provider_mode=fake")
    results: list[str] = []

    before = erp_invoice_count(erp)
    success_msg = f"demo-{run_id}-success"
    uploaded = upload_document(
        base_url=base,
        source_message_id=success_msg,
        pdf_bytes=scenario_pdf_bytes("SUCCESS", success_msg),
    )
    success_doc = wait_for_status(
        base,
        str(uploaded["document_id"]),
        wanted={"submitted"},
        timeout_seconds=60.0,
    )
    after_success = erp_invoice_count(erp)
    if after_success != before + 1:
        raise RuntimeError(
            f"expected exactly one new ERP invoice got delta={after_success - before}",
        )
    results.append(
        "submitted "
        f"document_id={success_doc['document_id']} "
        f"correlation_id={success_doc['correlation_id']} "
        f"erp_delta=1",
    )
    safe_print(results[-1])

    review_msg = f"demo-{run_id}-review"
    review_upload = upload_document(
        base_url=base,
        source_message_id=review_msg,
        pdf_bytes=scenario_pdf_bytes("LOW_CONFIDENCE", review_msg),
    )
    review_doc = wait_for_status(
        base,
        str(review_upload["document_id"]),
        wanted={"needs_review"},
        timeout_seconds=60.0,
    )
    results.append(
        "needs_review "
        f"document_id={review_doc['document_id']} "
        f"correlation_id={review_doc['correlation_id']}",
    )
    safe_print(results[-1])

    detail = get_review_detail(base, str(review_doc["document_id"]))
    edited = _edited_invoice_from_review(detail)
    before_approve = erp_invoice_count(erp)
    approve_review(
        base,
        str(review_doc["document_id"]),
        actor="demo-reviewer",
        reason="Corrected confidence for fictional demo invoice",
        edited_invoice=edited,
    )
    approved = wait_for_status(
        base,
        str(review_doc["document_id"]),
        wanted={"submitted"},
        timeout_seconds=60.0,
    )
    after_approve = erp_invoice_count(erp)
    if after_approve != before_approve + 1:
        raise RuntimeError("approve path did not create exactly one ERP invoice")
    results.append(
        "approved_submitted "
        f"document_id={approved['document_id']} "
        f"correlation_id={approved['correlation_id']} "
        f"erp_delta=1",
    )
    safe_print(results[-1])

    reject_msg = f"demo-{run_id}-reject"
    reject_upload = upload_document(
        base_url=base,
        source_message_id=reject_msg,
        pdf_bytes=scenario_pdf_bytes("LOW_CONFIDENCE", reject_msg),
    )
    reject_doc = wait_for_status(
        base,
        str(reject_upload["document_id"]),
        wanted={"needs_review"},
        timeout_seconds=60.0,
    )
    before_reject = erp_invoice_count(erp)
    reject_review(
        base,
        str(reject_doc["document_id"]),
        actor="demo-reviewer",
        reason="Rejected fictional duplicate supplier claim",
    )
    rejected = wait_for_status(
        base,
        str(reject_doc["document_id"]),
        wanted={"rejected"},
        timeout_seconds=30.0,
    )
    after_reject = erp_invoice_count(erp)
    if after_reject != before_reject:
        raise RuntimeError("reject path unexpectedly created an ERP invoice")
    results.append(
        "rejected "
        f"document_id={rejected['document_id']} "
        f"correlation_id={rejected['correlation_id']} "
        f"erp_delta=0",
    )
    safe_print(results[-1])

    dup_msg = f"demo-{run_id}-dup"
    dup_pdf = scenario_pdf_bytes("SUCCESS", dup_msg)
    first = upload_document(
        base_url=base,
        source_message_id=dup_msg,
        pdf_bytes=dup_pdf,
    )
    wait_for_status(base, str(first["document_id"]), wanted={"submitted"})
    before_dup = erp_invoice_count(erp)
    second = upload_document(
        base_url=base,
        source_message_id=dup_msg,
        pdf_bytes=dup_pdf,
    )
    if not second.get("idempotent_replay"):
        raise RuntimeError("duplicate upload did not return idempotent_replay")
    if str(second["document_id"]) != str(first["document_id"]):
        raise RuntimeError("duplicate upload returned a different document_id")
    after_dup = erp_invoice_count(erp)
    if after_dup != before_dup:
        raise RuntimeError("duplicate upload created an extra ERP invoice")
    results.append(
        "duplicate_idempotent "
        f"document_id={first['document_id']} "
        f"correlation_id={first['correlation_id']} "
        f"erp_delta=0",
    )
    safe_print(results[-1])

    fail_msg = f"demo-{run_id}-provider-failure"
    fail_upload = upload_document(
        base_url=base,
        source_message_id=fail_msg,
        pdf_bytes=scenario_pdf_bytes("PROVIDER_FAILURE", fail_msg),
    )
    failed = wait_for_status(
        base,
        str(fail_upload["document_id"]),
        wanted={"failed"},
        timeout_seconds=120.0,
        poll_interval=1.0,
    )
    failure = failed.get("failure") or {}
    if int(failed.get("retry_count", 0)) < 1:
        raise RuntimeError("provider failure path did not record retries")
    if not failure.get("dlq_present"):
        raise RuntimeError("provider failure path missing dlq_present")
    dlq = list_dlq(base)
    matching = [
        entry
        for entry in dlq.get("entries", [])
        if str(entry.get("document_id")) == str(failed["document_id"])
    ]
    if not matching:
        raise RuntimeError("DLQ inspection missing failed document")
    results.append(
        "retry_exhausted_dlq "
        f"document_id={failed['document_id']} "
        f"correlation_id={failed['correlation_id']} "
        f"retry_count={failed.get('retry_count')} "
        f"error_class={failure.get('error_class')} "
        f"dlq_depth={dlq.get('depth')}",
    )
    safe_print(results[-1])

    safe_print("demo_urls")
    safe_print(f"  ingestion={base}")
    safe_print(f"  erp_mock={erp}")
    safe_print(f"  review_ui={DEFAULT_REVIEW_UI}")
    safe_print("  prometheus=http://127.0.0.1:9091")
    safe_print("  grafana=http://127.0.0.1:3001")
    safe_print("  alertmanager=http://127.0.0.1:9093")
    safe_print("  jaeger=http://127.0.0.1:16686")
    safe_print(
        "note=provider_fallback is covered by offline tests; "
        "this demo exercises deterministic provider failure, retries, and DLQ",
    )
    safe_print(
        f"demo_ok cases={len(results)} "
        f"terminal_states={sorted(TERMINAL_STATES)}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
