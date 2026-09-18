import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

SENSITIVE_KEYS = frozenset(
    {
        "document_text",
        "prompt",
        "raw_path",
        "api_key",
        "token",
        "password",
        "authorization",
        "gemini_api_key",
        "invoice",
        "payload_json",
        "edited_invoice",
        "content",
        "file",
    },
)


def redact_value(key: str, value: Any) -> Any:
    lowered = key.lower()
    if lowered in SENSITIVE_KEYS:
        return "[redacted]"
    if isinstance(value, dict):
        return {
            str(item_key): redact_value(str(item_key), item_value)
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [redact_value(key, item) for item in value]
    return value


def build_log_record(
    *,
    service: str,
    event: str,
    level: str = "INFO",
    **fields: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "service": service,
        "level": level,
        "event": event,
    }
    for key, value in fields.items():
        if value is None:
            continue
        payload[key] = redact_value(key, value)
    return payload


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "service": getattr(record, "service", record.name),
            "level": record.levelname,
            "event": getattr(record, "event", record.getMessage()),
        }
        for key in (
            "document_id",
            "correlation_id",
            "workflow_state",
            "provider",
            "model_id",
            "retry_count",
            "error_class",
            "operation",
            "outcome",
        ):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = redact_value(key, value)
        if record.exc_info:
            payload["error_type"] = (
                record.exc_info[0].__name__
                if record.exc_info[0] is not None
                else "Exception"
            )
        return json.dumps(payload, sort_keys=True)


def configure_structured_logging(*, service: str, level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if getattr(root, "_docflow_json_configured", False):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    logging.LoggerAdapter(logging.getLogger(service), {"service": service})
    root._docflow_json_configured = True  # type: ignore[attr-defined]


def log_event(
    logger: logging.Logger,
    *,
    service: str,
    event: str,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    extra = {"service": service, "event": event}
    for key, value in fields.items():
        if value is None:
            continue
        extra[key] = redact_value(key, value)
    logger.log(level, event, extra=extra)
