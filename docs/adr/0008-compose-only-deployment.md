# ADR 0008: Compose-Only Local Deployment

## Status

Accepted

## Context

Step 10 allowed optional kind/minikube Helm packaging only if it added genuine, tested local deployment evidence without weakening the Compose demo. The Compose stack already runs ingestion, worker, ERP mock, review UI, PostgreSQL, Redis, Prometheus, Grafana, Alertmanager, OpenTelemetry Collector, and Jaeger with healthchecks, restart policies, and documented demo commands.

## Decision

Ship Docker Compose as the sole supported deployment target. Do not add Kubernetes, Helm, kind, minikube, or Terraform manifests in this repository.

## Consequences

Operators and reviewers use `make up`, `make migrate`, `make run-demo`, and related Makefile targets against Compose. Documentation must not claim cluster or cloud deployment evidence. Future Kubernetes packaging remains possible only as a separately requested, tested milestone.
