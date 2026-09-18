# DocFlow Observability Runbook

## Alert: DocFlowDLQDepthHigh

- Severity: warning
- Meaning: Redis dead-letter queue depth is greater than zero for at least one minute.
- Likely causes: transient provider or ERP failures exhausted Celery retries; worker misconfiguration; Redis connectivity issues during recovery.
- Immediate triage:
  1. Open Prometheus: http://localhost:9091/graph?g0.expr=docflow_dlq_depth
  2. Inspect DLQ: `curl -s http://localhost:8000/dlq`
  3. Check worker logs for `document_dlq` and `worker_transient_failure` events.
- Mitigation: identify the failed `document_id` and `error_class` from `/dlq`; fix the underlying provider/ERP issue; re-queue only after confirming the document is safe to retry.
- Verification: `docflow_dlq_depth` returns to 0 and no new `document_dlq` events appear.
- False positives: intentional DLQ demo traffic; brief depth spikes that clear before the `for: 1m` window.
- Rollback/escalation: restart `worker` with `docker compose restart worker` if the worker process is wedged; do not delete Redis data unless explicitly clearing a local demo queue.

## Alert: DocFlowProviderFailureRateHigh

- Severity: warning
- Meaning: Provider failure rate is elevated above the local demo threshold.
- Likely causes: simulated recoverable provider failures; misconfigured LLM mode; unavailable local Qwen endpoint; Gemini free-tier limits when live mode is intentionally enabled.
- Immediate triage:
  1. Query `sum by (provider,error_class) (rate(docflow_provider_failures_total[5m]))` in Prometheus.
  2. Inspect Grafana Quality dashboard: http://localhost:3001/d/docflow-quality
  3. Confirm LLM mode in Compose env (`LLM_MODE=test` for deterministic demos).
- Mitigation: keep default demos on fake provider; for live modes restore Qwen/Gemini availability or disable fallback experiments.
- Verification: provider failure rate drops below threshold and successful extraction metrics resume.
- False positives: deliberate failure injection during DLQ/retry demos.
- Rollback/escalation: set `LLM_MODE=test` and recreate ingestion/worker containers.

## Alert: DocFlowERPSubmissionFailureRateHigh

- Severity: critical
- Meaning: ERP invoice submission failures are a large share of recent ERP attempts.
- Likely causes: ERP mock down; network partition to `erp_mock`; intentional flaky ERP test client; non-transient ERP client errors.
- Immediate triage:
  1. Check ERP health: `curl -s http://localhost:8001/health`
  2. Query `sum by (outcome) (rate(docflow_erp_submissions_total[5m]))`
  3. Inspect worker logs for `document_failed` with `error_class=erp_submission_error` or retries with `erp_transient`.
- Mitigation: restore `erp_mock` (`docker compose up -d erp_mock`); wait for healthy status; allow Celery retries to complete.
- Verification: ERP success outcome increases and failure ratio falls below 0.2.
- False positives: scripted flaky ERP integration tests against a shared stack.
- Rollback/escalation: restart ERP mock; do not wipe PostgreSQL.

## Alert: DocFlowHumanReviewRateHigh

- Severity: warning
- Meaning: Documents are being routed to human review at a high rate for local demo traffic.
- Likely causes: low-confidence or rule-failure fixtures; provider output invalid path; auto-approval thresholds tightened; intentional needs-review demo uploads.
- Immediate triage:
  1. Query `rate(docflow_human_review_routed_total[5m])`
  2. Open review UI: http://localhost:8501
  3. Inspect validation failure categories in Grafana Quality dashboard.
- Mitigation: for demos, mix auto-approve and needs-review fixtures intentionally; for unexpected spikes, inspect issue codes and provider outcomes.
- Verification: review routing rate returns to expected demo levels and auto-approvals continue for valid invoices.
- False positives: review-heavy demo scripts.
- Rollback/escalation: restore default auto-approval settings from `.env.example`.

## Operational procedures

### Inspect `/metrics`

```bash
curl -s http://localhost:8000/metrics | head
curl -s http://localhost:8001/metrics | head
curl -s http://localhost:9101/metrics | grep '^docflow_'
```

Worker counters update immediately on `:9101`. Prometheus may lag one scrape (5s). Prefer checking worker metrics first, then:

```bash
sleep 6
curl -s 'http://localhost:9091/api/v1/query?query=docflow_documents_completed_total'
curl -s 'http://localhost:9091/api/v1/query?query=sum(docflow_human_review_routed_total)'
```

### Prometheus target health

Open http://localhost:9091/targets and confirm `ingestion`, `worker`, and `erp_mock` are UP.

### Grafana dashboards

- URL: http://localhost:3001
- Local login: `admin` / `admin` (local demo only)
- Dashboards: DocFlow Operational (`docflow-operational`), DocFlow Quality and Provider (`docflow-quality`)

### Alertmanager

Open http://localhost:9093 and inspect active/silenced alerts. Receiver `local` is intentionally empty for laptop inspection without external messaging.

### Inspect Redis DLQ safely

```bash
curl -s http://localhost:8000/dlq
docker compose exec -T redis redis-cli LLEN docflow:dlq
docker compose exec -T redis redis-cli LRANGE docflow:dlq -5 -1
```

Do not print or store raw invoice payloads from application logs.

### Trace one document by correlation ID

1. Upload a document and note `correlation_id` from the API response.
2. Search ingestion/worker JSON logs for that `correlation_id`.
3. Open Jaeger UI: http://localhost:16686
4. Search service `docflow-ingestion` or `docflow-worker` and locate spans tagged with the same `correlation_id`.
5. Expected span path includes upload/accept, Celery task, `worker.process_document`, `provider.extract`, and `erp.submit_invoice` when auto-approved.

### Identify provider fallback and error class

- Metrics: `docflow_provider_fallback_total`, `docflow_provider_failures_total`
- Logs: event `provider_fallback_triggered` with `provider` and `error_class`
- Traces: fallback appears as sequential extract attempts under the worker span when enabled

### Safely restart an unhealthy Compose service

```bash
docker compose ps
docker compose restart worker
docker compose up -d --no-deps erp_mock
```

Do not run `docker compose down -v` unless you intentionally want to destroy local Postgres/Redis demo data.
