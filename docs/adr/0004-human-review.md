# ADR 0004: Human Review Commands and Worker-Owned Resume

## Status

Accepted

## Context

Documents that fail deterministic auto-approval must be reviewed by a human. The Streamlit UI needs approve, edit-then-approve, and reject actions with auditable actor identity, while Step 5 already established that ERP submission belongs to Celery workers and must remain idempotent under retries and duplicate delivery.

## Decision

Expose thin FastAPI review command endpoints that call a domain review service. The UI only collects reviewer identity, reason, and optional edited invoice JSON. Approve transitions `needs_review` to `approved`, records audit events (including before/after payloads for edits), and enqueues the existing worker task to submit exactly once. Edit-then-approve revalidates with existing business rules before approval. Reject transitions to `rejected` and never enqueues ERP work. Reviewer identity is an explicit local demo field, not production authentication.

## Consequences

Human review cannot bypass validation, state transitions, audit logging, or ERP idempotency. Duplicate approve requests are safe. The Streamlit service remains a presentation surface over the ingestion API.
