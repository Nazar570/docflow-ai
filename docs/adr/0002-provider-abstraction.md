# ADR 0002: Provider Abstraction

## Status

Accepted

## Context

DocFlow AI must support a deterministic fake provider for tests, a local Qwen endpoint for zero-cost operation, and an optional Gemini API path with safe fallback. Business rules and ERP actions must remain deterministic and independent of provider choice.

## Decision

Expose one typed `LLMProvider` contract with Pydantic request and result models. Select providers through configuration modes (`test`, `local`, `gemini_preferred`, `qwen_preferred`). Route recoverable Gemini failures to local Qwen when fallback is enabled, and persist the provider used plus fallback reason. Validate all provider JSON with the shared invoice schema and allow one corrective extraction attempt after invalid structured output.

## Consequences

Provider changes do not require workflow or rules changes. Default automated tests remain offline through the fake provider. Live Qwen and Gemini require explicit local configuration and are excluded from default test runs.
