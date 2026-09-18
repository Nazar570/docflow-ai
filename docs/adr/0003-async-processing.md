# ADR 0003: Asynchronous Processing with Redis and Celery

## Status

Accepted

## Context

Synchronous invoice processing blocked ingestion on OCR, LLM extraction, validation, and ERP submission. Duplicate task delivery and transient dependency failures required bounded retries, an inspectable dead-letter path, and idempotent worker ownership without changing provider or business-rule contracts.

## Decision

Accept uploads with HTTP 202 after durable `queued` persistence and enqueue a Celery task whose payload contains only document id and correlation id. Workers claim `queued` documents into `processing`, load authoritative state from PostgreSQL, and execute the existing workflow. Retry only explicit transient failures with exponential backoff. After retry exhaustion, push a safe failure envelope to a Redis list DLQ and mark the document `failed`. Preserve ERP and upload idempotency so duplicate deliveries cannot create duplicate invoices.

## Consequences

Ingestion remains thin and fast. Progress is visible through the document status endpoint including retry metadata and DLQ presence. Default tests stay offline using the fake provider and synchronous test enqueue hooks or eager worker execution against local Redis and PostgreSQL.
