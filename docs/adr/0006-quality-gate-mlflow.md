# ADR 0006: Local Quality Gate and MLflow Tracking

## Status

Accepted

## Context

Step 7 produced deterministic evaluation reports and a degraded fixture. The project needs local experiment tracking for comparable runs and a fail-fast quality gate that prevents silent regressions without requiring live providers or remote CI activation.

## Decision

Use local SQLite-backed MLflow tracking under `artifacts/mlflow.db` with opt-in `--log-mlflow` and local artifact files under `artifacts/mlruns`. Default quality gate runs lint, types, tests, fake-provider baseline evaluation, and threshold comparison against an intentional baseline config derived from a measured deterministic run. Latency and live-provider calls are excluded from default gating. The degraded fixture is used only to prove threshold failure. The same local gate command is designed to be CI-ready later, but GitHub Actions are not activated in this step.

## Consequences

Baseline fake-provider evaluation remains the source of truth for local release readiness. MLflow UI inspection is optional via `mlflow ui --backend-store-uri sqlite:///artifacts/mlflow.db`. Generated MLflow state and evaluation reports stay local and gitignored. Local SQLite was chosen because current MLflow versions treat the filesystem tracking backend as maintenance-mode and require an explicit opt-out or database backend for reliable local operation.
