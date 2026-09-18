import logging
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

_TRACING_READY = False


def tracing_enabled() -> bool:
    value = os.environ.get("OTEL_TRACES_ENABLED", "true").strip().lower()
    return value in {"1", "true", "yes", "on"}


def setup_tracing(*, service_name: str) -> bool:
    global _TRACING_READY
    if _TRACING_READY:
        return True
    if not tracing_enabled():
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        endpoint = os.environ.get(
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "http://localhost:4318",
        ).rstrip("/")
        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _TRACING_READY = True
        return True
    except Exception:
        logger.debug("otel_setup_failed", exc_info=False)
        return False


def instrument_fastapi(app: Any) -> None:
    if not tracing_enabled():
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except Exception:
        logger.debug("fastapi_instrument_failed", exc_info=False)


def instrument_celery() -> None:
    if not tracing_enabled():
        return
    try:
        from opentelemetry.instrumentation.celery import CeleryInstrumentor

        CeleryInstrumentor().instrument()  # type: ignore[no-untyped-call]
    except Exception:
        logger.debug("celery_instrument_failed", exc_info=False)


def instrument_httpx() -> None:
    if not tracing_enabled():
        return
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except Exception:
        logger.debug("httpx_instrument_failed", exc_info=False)


def get_tracer(name: str = "docflow") -> Any:
    try:
        from opentelemetry import trace

        return trace.get_tracer(name)
    except Exception:
        return None


@contextmanager
def start_span(
    name: str,
    *,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Any]:
    tracer = get_tracer()
    if tracer is None or not tracing_enabled():
        yield None
        return
    span_cm: Any | None = None
    span: Any | None = None
    try:
        span_cm = tracer.start_as_current_span(name)
        span = span_cm.__enter__()
    except Exception:
        yield None
        return
    try:
        if attributes and span is not None:
            for key, value in attributes.items():
                if value is None:
                    continue
                if key in {
                    "document_text",
                    "prompt",
                    "api_key",
                    "invoice",
                    "payload",
                }:
                    continue
                try:
                    span.set_attribute(key, value)
                except Exception:
                    continue
        yield span
    finally:
        if span_cm is not None:
            try:
                span_cm.__exit__(*sys.exc_info())
            except Exception:
                logger.debug("otel_span_exit_failed", exc_info=False)


def safe_span_attributes(
    *,
    document_id: str | None = None,
    correlation_id: str | None = None,
    workflow_state: str | None = None,
    provider: str | None = None,
    model_family: str | None = None,
    retry_count: int | None = None,
    error_class: str | None = None,
    outcome: str | None = None,
    http_status_class: str | None = None,
) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    if document_id is not None:
        attrs["document_id"] = document_id
    if correlation_id is not None:
        attrs["correlation_id"] = correlation_id
    if workflow_state is not None:
        attrs["workflow_state"] = workflow_state
    if provider is not None:
        attrs["provider"] = provider
    if model_family is not None:
        attrs["model_family"] = model_family
    if retry_count is not None:
        attrs["retry_count"] = retry_count
    if error_class is not None:
        attrs["error_class"] = error_class
    if outcome is not None:
        attrs["outcome"] = outcome
    if http_status_class is not None:
        attrs["http_status_class"] = http_status_class
    return attrs
