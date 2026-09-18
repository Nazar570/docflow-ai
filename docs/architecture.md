# DocFlow AI Architecture

## Scope

DocFlow AI is a local Compose demo of an invoice document-to-action pipeline. It is production-inspired for portfolio evidence: typed contracts, async workers, idempotency, human review, evaluation, quality gate, and observability. It is not a claimed multi-region production deployment.

## Supported deployment

Docker Compose is the only supported runtime packaging in this repository. Kubernetes, Helm, kind, minikube, and Terraform were evaluated for Step 10 and intentionally omitted: they would not strengthen the already complete Compose demo and would add untested surface area.

## Runtime components

```
                 ┌──────────────┐
   PDF upload ──►│  ingestion   │──► PostgreSQL
                 │  FastAPI     │──► Redis/Celery
                 └──────┬───────┘
                        │
                        ▼
                 ┌──────────────┐     ┌────────────┐
                 │    worker    │────►│  ERP mock  │
                 │ Celery solo  │     └────────────┘
                 └──────┬───────┘
                        │ needs_review
                        ▼
                 ┌──────────────┐
                 │  review UI   │
                 │  Streamlit   │
                 └──────────────┘

Observability sidecar path (non-blocking):
  apps ──OTLP HTTP──► otel-collector ──► jaeger
  apps /metrics ────► prometheus ──► grafana
                 └──► alertmanager
```

## Processing contract

1. `POST /documents` persists the upload, enqueues work, returns `202` with `document_id` and `correlation_id`.
2. The worker owns OCR/text extraction, provider extraction, validation, decisioning, and ERP submission.
3. Auto-approved invoices submit exactly once to the ERP mock using an idempotency key.
4. Review-bound invoices stop at `needs_review` until approve/reject.
5. Recoverable failures retry with bounded exponential backoff, then land in a Redis list DLQ with `failed` status.

## Provider modes

- Default Compose and automation: `LLM_MODE=test` + fake provider scenarios embedded in fictional PDF text markers.
- Optional live Qwen through a separately started llama.cpp OpenAI-compatible endpoint.
- Optional Gemini through a local `.env` API key, never logged or committed.
- Configuration-driven fallback exists between Gemini and Qwen when explicitly enabled; it is not part of default demo/load-test claims.

## Evaluation and gate

Offline golden cases under `llmops/datasets/v1` drive deterministic fake-provider evaluation. Optional local MLflow uses SQLite under `artifacts/`. `make quality-gate` compares baseline metrics to `llmops/baselines/v1_fake_baseline.json`. Degraded fixture mode must fail the gate.

## Observability join key

`correlation_id` links API responses, structured logs, and trace spans. Metric labels stay coarse (provider, outcome, error class) and exclude document payloads, prompts, and secrets.

## Data and artifacts

Generated local data stays under ignored paths: `data/`, `artifacts/`, model files, `.env`. Seed/demo/load-test outputs write under `artifacts/` and must not pollute source trees.
