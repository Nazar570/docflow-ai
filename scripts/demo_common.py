import hashlib
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from uuid import uuid4

from services.worker.extraction.pdf_samples import build_pdf_bytes

DEFAULT_INGESTION = "http://127.0.0.1:8000"
DEFAULT_ERP = "http://127.0.0.1:8001"
DEFAULT_REVIEW_UI = "http://127.0.0.1:8501"
TERMINAL_STATES = frozenset(
    {"submitted", "needs_review", "rejected", "failed"},
)
DEMO_SEED_DIR = Path("artifacts/demo")


def new_run_id() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid4().hex[:8]


def unique_invoice_number(marker: str) -> str:
    digest = hashlib.sha256(marker.encode()).hexdigest()[:12]
    return f"INV-{digest}"


def scenario_pdf_bytes(scenario: str, marker: str) -> bytes:
    invoice_number = unique_invoice_number(marker)
    text = (
        f"DOC FLOW SCENARIO:{scenario.upper()} "
        f"INVOICE_NUMBER:{invoice_number} "
        f"fictional invoice {marker}"
    )
    return build_pdf_bytes(text)


def write_seed_pdfs(seed_dir: Path | None = None) -> Path:
    target = seed_dir or DEMO_SEED_DIR
    target.mkdir(parents=True, exist_ok=True)
    mapping = {
        "success.pdf": "SUCCESS",
        "low_confidence.pdf": "LOW_CONFIDENCE",
        "provider_failure.pdf": "PROVIDER_FAILURE",
        "invalid_total.pdf": "INVALID_TOTAL",
    }
    for filename, scenario in mapping.items():
        path = target / filename
        path.write_bytes(scenario_pdf_bytes(scenario, filename))
    return target.resolve()


def http_json(
    method: str,
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> tuple[int, Any]:
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers or {},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            payload: Any
            if body:
                payload = json.loads(body.decode("utf-8"))
            else:
                payload = None
            return int(response.status), payload
    except urllib.error.HTTPError as exc:
        body = exc.read()
        payload = None
        if body:
            try:
                payload = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                payload = {"raw_status": exc.code}
        return int(exc.code), payload


def http_get_json(url: str, *, timeout: float = 30.0) -> tuple[int, Any]:
    return http_json("GET", url, timeout=timeout)


def http_status(url: str, *, timeout: float = 30.0) -> int:
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def wait_healthy(url: str, *, timeout_seconds: float = 90.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "unreachable"
    while time.monotonic() < deadline:
        try:
            status = http_status(url, timeout=5.0)
            if status == 200:
                return
            last_error = f"status={status}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(1.0)
    raise RuntimeError(f"health check failed for {url}: {last_error}")


def upload_document(
    *,
    base_url: str,
    source_message_id: str,
    pdf_bytes: bytes,
    filename: str = "invoice.pdf",
) -> dict[str, Any]:
    boundary = f"----docflow{uuid4().hex}"
    parts: list[bytes] = []
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="source_message_id"\r\n\r\n'
            f"{source_message_id}\r\n"
        ).encode(),
    )
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: application/pdf\r\n\r\n"
        ).encode()
        + pdf_bytes
        + b"\r\n",
    )
    parts.append(f"--{boundary}--\r\n".encode())
    body = b"".join(parts)
    status, payload = http_json(
        "POST",
        f"{base_url.rstrip('/')}/documents",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        timeout=60.0,
    )
    if status != 202 or not isinstance(payload, dict):
        raise RuntimeError(f"upload failed status={status}")
    return payload


def get_document(base_url: str, document_id: str) -> dict[str, Any]:
    status, payload = http_get_json(f"{base_url.rstrip('/')}/documents/{document_id}")
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"document status failed status={status}")
    return payload


def wait_for_status(
    base_url: str,
    document_id: str,
    *,
    wanted: set[str],
    timeout_seconds: float = 120.0,
    poll_interval: float = 0.5,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = get_document(base_url, document_id)
        if str(last.get("status", "")) in wanted:
            return last
        time.sleep(poll_interval)
    raise RuntimeError(
        f"timeout waiting for {sorted(wanted)} document_id={document_id} "
        f"last_status={None if last is None else last.get('status')}",
    )


def erp_invoice_count(erp_base_url: str) -> int:
    status, payload = http_get_json(
        f"{erp_base_url.rstrip('/')}/_debug/invoice_count",
    )
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"erp invoice count failed status={status}")
    return int(payload["count"])


def get_review_detail(base_url: str, document_id: str) -> dict[str, Any]:
    status, payload = http_get_json(f"{base_url.rstrip('/')}/reviews/{document_id}")
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"review detail failed status={status}")
    return payload


def approve_review(
    base_url: str,
    document_id: str,
    *,
    actor: str,
    reason: str,
    edited_invoice: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"actor": actor, "reason": reason}
    if edited_invoice is not None:
        body["edited_invoice"] = edited_invoice
    encoded = json.dumps(body).encode()
    status, payload = http_json(
        "POST",
        f"{base_url.rstrip('/')}/reviews/{document_id}/approve",
        data=encoded,
        headers={"Content-Type": "application/json"},
    )
    if status not in {200, 202} or not isinstance(payload, dict):
        raise RuntimeError(f"approve failed status={status}")
    return payload


def reject_review(
    base_url: str,
    document_id: str,
    *,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    encoded = json.dumps({"actor": actor, "reason": reason}).encode()
    status, payload = http_json(
        "POST",
        f"{base_url.rstrip('/')}/reviews/{document_id}/reject",
        data=encoded,
        headers={"Content-Type": "application/json"},
    )
    if status not in {200, 202} or not isinstance(payload, dict):
        raise RuntimeError(f"reject failed status={status}")
    return payload


def list_dlq(base_url: str) -> dict[str, Any]:
    status, payload = http_get_json(f"{base_url.rstrip('/')}/dlq")
    if status != 200 or not isinstance(payload, dict):
        raise RuntimeError(f"dlq list failed status={status}")
    return payload


def safe_print(message: str) -> None:
    print(message, flush=True)
