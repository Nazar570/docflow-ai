# ADR 0005: Deterministic Golden Evaluation

## Status

Accepted

## Context

DocFlow needs an honest, reproducible way to measure extraction schema validity, field accuracy, and deterministic decision outcomes without live LLM calls. The product already exposes a typed provider contract and deterministic business rules that evaluation should exercise directly.

## Decision

Maintain a small versioned fictional golden dataset under `llmops/datasets`. Evaluate cases in-process through the fake provider and existing `decide_workflow` rules. Score schema validity, normalized field accuracy, decision accuracy, case success, provider-failure fixtures, and local latency. Persist structured JSON reports under `artifacts/evaluations/`. Provide a degraded fake fixture that lowers measured accuracy without changing production defaults.

## Consequences

Default evaluation remains offline and fast. Reports are ready for Step 8 MLflow tracking and quality gates. Results are explicitly limited to this small fictional dataset and must not be presented as production accuracy.
