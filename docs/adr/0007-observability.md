# ADR 0007: Local Observability Stack

## Status

Accepted

## Context

Step 8 completed deterministic evaluation and quality gates. The workflow now needs production-style metrics, structured logs, distributed traces, dashboards, and actionable alerts that can be demonstrated on one laptop without paid SaaS.

## Decision

Expose Prometheus metrics from ingestion, worker, and ERP mock with low-cardinality labels only. Use structured JSON logs joined by correlation ID. Export OpenTelemetry traces through a local Collector to Jaeger. Provision Grafana against Prometheus and Alertmanager with an empty local receiver. Telemetry failures are best-effort and must not alter invoice processing. Default tests disable external trace export.

## Consequences

Operators can inspect throughput, latency, provider failures, fallbacks, retries, DLQ depth, review routing, and ERP outcomes locally. Alerts map 1:1 to `docs/runbook.md`. Kubernetes and cloud telemetry remain out of scope until Step 10 packaging decisions.
