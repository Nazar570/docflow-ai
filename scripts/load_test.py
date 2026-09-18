from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.demo_common import (
    DEFAULT_ERP,
    DEFAULT_INGESTION,
    erp_invoice_count,
    get_document,
    new_run_id,
    safe_print,
    scenario_pdf_bytes,
    upload_document,
    wait_healthy,
)

TERMINAL = frozenset({"submitted", "needs_review", "rejected", "failed"})


@dataclass
class Sample:
    source_message_id: str
    document_id: str | None
    correlation_id: str | None
    upload_ok: bool
    ingress_ms: float
    e2e_ms: float | None
    final_status: str | None
    error: str | None


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _run_one(
    *,
    index: int,
    run_id: str,
    base_url: str,
    timeout_seconds: float,
) -> Sample:
    source_message_id = f"load-{run_id}-{index:04d}"
    pdf_bytes = scenario_pdf_bytes("SUCCESS", source_message_id)
    started = time.perf_counter()
    try:
        uploaded = upload_document(
            base_url=base_url,
            source_message_id=source_message_id,
            pdf_bytes=pdf_bytes,
            filename=f"load-{index:04d}.pdf",
        )
        ingress_ms = (time.perf_counter() - started) * 1000.0
        document_id = str(uploaded["document_id"])
        correlation_id = str(uploaded["correlation_id"])
    except Exception as exc:
        return Sample(
            source_message_id=source_message_id,
            document_id=None,
            correlation_id=None,
            upload_ok=False,
            ingress_ms=(time.perf_counter() - started) * 1000.0,
            e2e_ms=None,
            final_status=None,
            error=type(exc).__name__,
        )

    deadline = time.monotonic() + timeout_seconds
    final_status: str | None = None
    error: str | None = None
    while time.monotonic() < deadline:
        try:
            status_body = get_document(base_url, document_id)
            status_value = str(status_body.get("status", ""))
            if status_value in TERMINAL:
                final_status = status_value
                break
        except Exception as exc:
            error = type(exc).__name__
        time.sleep(0.25)
    e2e_ms = (time.perf_counter() - started) * 1000.0
    if final_status is None and error is None:
        error = "timeout"
    return Sample(
        source_message_id=source_message_id,
        document_id=document_id,
        correlation_id=correlation_id,
        upload_ok=True,
        ingress_ms=ingress_ms,
        e2e_ms=e2e_ms if final_status is not None else None,
        final_status=final_status,
        error=error,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.load_test")
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout-seconds", type=float, default=90.0)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--base-url", default=DEFAULT_INGESTION)
    parser.add_argument("--erp-url", default=DEFAULT_ERP)
    parser.add_argument("--report-dir", default="artifacts/load_tests")
    args = parser.parse_args(argv)

    if args.requests < 1 or args.concurrency < 1:
        raise SystemExit("requests and concurrency must be >= 1")

    run_id = new_run_id()
    wait_healthy(f"{args.base_url.rstrip('/')}/health")
    wait_healthy(f"{args.erp_url.rstrip('/')}/health")

    if args.warmup > 0:
        for index in range(args.warmup):
            _run_one(
                index=9000 + index,
                run_id=f"warmup-{run_id}",
                base_url=args.base_url,
                timeout_seconds=args.timeout_seconds,
            )

    before_erp = erp_invoice_count(args.erp_url)
    wall_start = time.perf_counter()
    samples: list[Sample] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(
                _run_one,
                index=index,
                run_id=run_id,
                base_url=args.base_url,
                timeout_seconds=args.timeout_seconds,
            )
            for index in range(args.requests)
        ]
        for future in as_completed(futures):
            samples.append(future.result())
    wall_seconds = time.perf_counter() - wall_start
    after_erp = erp_invoice_count(args.erp_url)

    accepted = sum(1 for sample in samples if sample.upload_ok)
    submitted = sum(1 for sample in samples if sample.final_status == "submitted")
    needs_review = sum(
        1 for sample in samples if sample.final_status == "needs_review"
    )
    failed = sum(1 for sample in samples if sample.final_status == "failed")
    errors = [sample for sample in samples if sample.error is not None]
    error_categories: dict[str, int] = {}
    for sample in errors:
        key = sample.error or "unknown"
        error_categories[key] = error_categories.get(key, 0) + 1

    ingress_values = [sample.ingress_ms for sample in samples if sample.upload_ok]
    e2e_values = [
        sample.e2e_ms for sample in samples if sample.e2e_ms is not None
    ]
    throughput = submitted / wall_seconds if wall_seconds > 0 else 0.0

    report: dict[str, Any] = {
        "execution_timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "test_version": "docflow-load-test-v1",
        "command": (
            f"python -m scripts.load_test --requests {args.requests} "
            f"--concurrency {args.concurrency} "
            f"--timeout-seconds {args.timeout_seconds} --warmup {args.warmup}"
        ),
        "host_environment": {
            "os": platform.system(),
            "os_release": platform.release(),
            "platform": platform.platform(),
            "python_version": sys.version.split()[0],
            "cpu_count_logical": os.cpu_count(),
        },
        "compose_context": {
            "deployment": "docker-compose",
            "provider_mode": "fake",
            "llm_mode": "test",
            "scenario": "SUCCESS",
            "ingestion_url": args.base_url,
            "erp_url": args.erp_url,
        },
        "workload": {
            "requests": args.requests,
            "concurrency": args.concurrency,
            "timeout_seconds": args.timeout_seconds,
            "warmup_requests": args.warmup,
            "success_definition": (
                "HTTP 202 upload accepted and document reaches terminal "
                "status submitted via fake provider"
            ),
        },
        "results": {
            "accepted_upload_count": accepted,
            "submitted_count": submitted,
            "needs_review_count": needs_review,
            "failed_count": failed,
            "error_count": len(errors),
            "error_categories": error_categories,
            "erp_invoice_delta": after_erp - before_erp,
            "wall_clock_seconds": round(wall_seconds, 3),
            "throughput_submitted_per_second": round(throughput, 3),
            "ingress_latency_ms": {
                "mean": (
                    round(statistics.fmean(ingress_values), 3)
                    if ingress_values
                    else None
                ),
                "p50": (
                    None
                    if _percentile(ingress_values, 0.50) is None
                    else round(_percentile(ingress_values, 0.50) or 0.0, 3)
                ),
                "p95": (
                    None
                    if _percentile(ingress_values, 0.95) is None
                    else round(_percentile(ingress_values, 0.95) or 0.0, 3)
                ),
            },
            "e2e_terminal_latency_ms": {
                "sample_count": len(e2e_values),
                "mean": (
                    round(statistics.fmean(e2e_values), 3) if e2e_values else None
                ),
                "p50": (
                    None
                    if _percentile(list(e2e_values), 0.50) is None
                    else round(_percentile(list(e2e_values), 0.50) or 0.0, 3)
                ),
                "p95": (
                    None
                    if _percentile(list(e2e_values), 0.95) is None
                    else round(_percentile(list(e2e_values), 0.95) or 0.0, 3)
                ),
            },
        },
        "methodology": (
            "Fixed-count concurrent uploads of fictional SUCCESS-scenario PDFs "
            "against the local Compose stack in fake-provider mode. Ingress "
            "latency is measured to HTTP 202. End-to-end latency is measured to "
            "document terminal status. Warm-up requests are excluded from report "
            "aggregates. ERP invoice delta is read from the local ERP mock "
            "debug counter."
        ),
        "limitations": [
            "Laptop-local measurement only",
            "Fake provider only; not live Qwen or Gemini",
            "Not production throughput, SLA, or SLO evidence",
            "Single-worker Celery solo pool Compose defaults",
            "Shared laptop CPU/IO contention may affect results",
        ],
        "run_id": run_id,
    }

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    report_path = report_dir / f"load_test_{stamp}.json"
    latest_path = report_dir / "latest.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    latest_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    safe_print(f"load_test_report={report_path}")
    throughput = report["results"]["throughput_submitted_per_second"]
    ingress_p50 = report["results"]["ingress_latency_ms"]["p50"]
    e2e_p50 = report["results"]["e2e_terminal_latency_ms"]["p50"]
    safe_print(
        "load_test_summary "
        f"accepted={accepted} submitted={submitted} errors={len(errors)} "
        f"erp_delta={after_erp - before_erp} "
        f"throughput_submitted_per_s={throughput} "
        f"ingress_p50_ms={ingress_p50} "
        f"e2e_p50_ms={e2e_p50}",
    )
    if accepted != args.requests or submitted != args.requests or errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
