# DocFlow AI

Local-first invoice automation with a typed extraction contract, Redis/Celery async processing, human review, deterministic evaluation, a local quality gate, and a Compose observability stack. Default runs stay offline on a fake LLM provider; live Qwen (llama.cpp) and Gemini are optional manual modes only.

---

## Stack

- **API:** FastAPI + Uvicorn
- **Worker:** Celery + Redis (`--pool=solo`)
- **Persistence:** PostgreSQL 16 + SQLAlchemy + Alembic
- **ERP mock:** FastAPI service with idempotent invoice submission
- **Review UI:** Streamlit
- **Generation:** Fake provider by default; optional Gemini API with local Qwen fallback through llama.cpp
- **Evaluation:** Offline golden dataset + local quality gate
- **Tracking:** Optional local MLflow (SQLite)
- **Observability:** Prometheus, Grafana, Alertmanager, OpenTelemetry Collector, Jaeger
- **Deployment:** Docker Compose only (no Kubernetes/Helm in this repository)

---

## Setup

```bash
git clone https://github.com/Nazar570/docflow-ai.git
cd docflow-ai
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp -n .env.example .env
make up
make migrate
```

Compose defaults use `LLM_MODE=test` / fake provider. Keep any Gemini key in local `.env` only; never log or commit it. Qwen requires a separately started llama.cpp OpenAI-compatible server (not Ollama).

---

## Services

| Service | Host URL | Notes |
|---|---|---|
| Ingestion API | http://localhost:8000 | uploads, status, review API, DLQ, metrics |
| ERP mock | http://localhost:8001 | fictional suppliers/POs/invoices |
| Review UI | http://localhost:8501 | Streamlit console |
| Worker metrics | http://localhost:9101/metrics | Prometheus scrape target |
| Prometheus | http://localhost:9091 | host `9091` → container `9090` |
| Grafana | http://localhost:3001 | local-only `admin` / `admin`; anonymous Viewer enabled |
| Alertmanager | http://localhost:9093 | alert routing |
| Jaeger | http://localhost:16686 | traces |
| OTLP Collector | localhost:4317 (gRPC), localhost:4318 (HTTP) | apps export here |
| PostgreSQL | localhost:5433 | Compose DB |
| Redis | localhost:6379 | broker + DLQ |

---

## Endpoints

Ingestion:

| Method | Path | Role |
|---|---|---|
| POST | `/documents` | async upload (`source_message_id`, PDF) → `202` |
| GET | `/documents/{document_id}` | status, retry/DLQ summary |
| GET | `/reviews` | needs-review listing |
| GET | `/reviews/{document_id}` | review detail |
| POST | `/reviews/{document_id}/approve` | approve / edit+approve |
| POST | `/reviews/{document_id}/reject` | reject (no ERP) |
| GET | `/dlq` | inspect dead-letter entries |
| GET | `/health` | liveness |
| GET | `/metrics` | Prometheus metrics |

ERP mock: `GET /health`, `GET /metrics`, supplier/PO lookups, `POST /invoices` with `Idempotency-Key`.

---

## Pipeline flow

```
PDF upload
   │
   ▼
ingestion API ──202──► PostgreSQL (queued) + Celery task
   │
   ▼
worker (OCR/text → LLM extract → validate → decide)
   │
   ├── auto-approve ──► ERP mock ──► submitted
   ├── needs_review ──► Streamlit / review API
   │                       ├── approve ──► ERP mock ──► submitted
   │                       └── reject  ──► rejected (no ERP)
   └── recoverable failure ──► bounded retries ──► DLQ + failed
```

Duplicate `source_message_id` uploads return the original document (`idempotent_replay`) and do not create a second ERP invoice.

---

## Commands

```bash
make up                 # build + start Compose stack
make ps                 # service status
make logs               # recent Compose logs
make down               # stop stack (volumes retained)
make migrate            # alembic upgrade head
make seed-demo          # write fictional demo PDFs under artifacts/demo/
make run-demo           # end-to-end fake-provider demo
make smoke              # health + one submitted path
make test               # deterministic offline pytest
make lint
make typecheck
make check              # lint + typecheck + test
make eval               # baseline fake evaluation
make eval-degraded      # degraded fixture evaluation
make quality-gate       # local gate (baseline)
make load-test          # laptop fake-provider load measurement
```

Quality gate degraded expectation (non-zero exit):

```bash
make quality-gate QUALITY_GATE_FIXTURE=degraded
```

Optional MLflow (local SQLite only):

```bash
python -m llmops.eval.run --provider fake --dataset-version v1 --log-mlflow
python -m llmops.eval.run --provider fake --dataset-version v1 --fixture degraded --log-mlflow
mlflow ui --backend-store-uri sqlite:///artifacts/mlflow.db --port 5000
```

---

## Demo

`make seed-demo` writes fictional PDFs only. `make run-demo` exercises the running Compose stack:

1. valid invoice → `submitted` + exactly one ERP invoice
2. low-confidence invoice → `needs_review`
3. edit + approve → `submitted` + one ERP invoice
4. reject → `rejected` + no ERP invoice
5. duplicate upload → idempotent replay, no extra ERP invoice
6. provider failure → retries → `failed` + DLQ entry

Repeated demos use unique `source_message_id` values and do not wipe the database.

---

## Evaluation

Golden dataset `llmops/datasets/v1` is small and fictional. Default evaluation and `make quality-gate` use the fake provider only. They do not require Gemini, Qwen, llama.cpp, or Internet access. Metrics are regression checks for this repository, not live-model or customer-document accuracy claims. Baseline thresholds live in `llmops/baselines/v1_fake_baseline.json`. Reports land under ignored `artifacts/evaluations/`.

---

## Human review

Streamlit at http://localhost:8501 lists `needs_review` documents. Approve, edit-then-approve, and reject require an explicit demo actor and reason. Approvals resume through the worker to ERP; rejects never call ERP. Reviewer identity is a local demo field, not an auth system.

---

## Observability

| Signal | Where |
|---|---|
| Metrics | `/metrics` on ingestion, worker `:9101`, ERP mock; Prometheus http://localhost:9091 |
| Logs | structured JSON with `correlation_id` |
| Traces | OTLP → Collector → Jaeger http://localhost:16686 |
| Dashboards | Grafana http://localhost:3001 (`docflow-operational`, `docflow-quality`) |
| Alerts | Alertmanager http://localhost:9093 + `docs/runbook.md` |

Telemetry failures must not break invoice processing. Alert responses are documented in `docs/runbook.md`.

---

## Load test

```bash
make load-test
```

Default workload: 20 SUCCESS-scenario uploads, concurrency 4, fake provider, Compose stack. Report: `artifacts/load_tests/latest.json` (gitignored).

Measured locally on 2026-09-18 (`docflow-load-test-v1`, fake/test mode, warm-up 1 excluded from aggregates):

| Metric | Value |
|---|---|
| Accepted / submitted | 20 / 20 |
| Errors | 0 |
| ERP invoice delta | 20 |
| Wall clock | 1.542 s |
| Throughput (submitted/s) | 12.974 |
| Ingress latency p50 / p95 / mean | 21.31 / 82.22 / 29.365 ms |
| E2E terminal latency p50 / p95 / mean | 294.135 / 353.804 / 298.796 ms |

Laptop-local fake-provider results only. Not production throughput, SLA, or SLO evidence.

---

## LLM modes

| Mode | Primary | Fallback |
|---|---|---|
| `test` (default) | fake | none |
| `local` | Qwen via llama.cpp | none |
| `gemini_preferred` | Gemini | Qwen when `LLM_FALLBACK_ENABLED=true` |
| `qwen_preferred` | Qwen | Gemini when `LLM_FALLBACK_ENABLED=true` |

Default tests, demo completion, quality gate, and load-test claims stay on fake/test.

Optional live Qwen: start llama.cpp with an OpenAI-compatible server on `QWEN_BASE_URL`, set `LLM_MODE=local` / `LLM_PRIMARY_PROVIDER=qwen` for ingestion and worker, recreate those services.

Optional Gemini: set `GEMINI_API_KEY` in local `.env` only, choose `gemini_preferred`, recreate services. Never print the key.

---

## Configuration

See `.env.example` for every supported local setting, including `TOTALS_TOLERANCE`, Celery retry bounds, MLflow tracking URI, and OTLP endpoint. Compose injects fake-provider defaults for the demo stack.

---

## Architecture and ADRs

- `docs/architecture.md` — Compose-centered system view
- `docs/adr/` — provider abstraction, async processing, human review, evaluation, quality gate/MLflow, observability, Compose-only deployment
- `docs/runbook.md` — alert responses

Kubernetes, Helm, kind, minikube, and Terraform are intentionally out of scope. Compose is the supported deployment target for this portfolio project.
