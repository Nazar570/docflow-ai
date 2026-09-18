import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import redis

DLQ_KEY = "docflow:dlq"


class DeadLetterQueue:
    def __init__(self, *, redis_url: str) -> None:
        self._client = redis.Redis.from_url(redis_url, decode_responses=True)

    def close(self) -> None:
        self._client.close()

    def push(
        self,
        *,
        document_id: UUID,
        correlation_id: UUID,
        retry_count: int,
        error_class: str,
        error_message: str,
    ) -> None:
        envelope = {
            "document_id": str(document_id),
            "correlation_id": str(correlation_id),
            "retry_count": retry_count,
            "error_class": error_class,
            "error_message": error_message,
            "failed_at": datetime.now(UTC).isoformat(),
        }
        self._client.rpush(DLQ_KEY, json.dumps(envelope))

    def list_entries(self, *, limit: int = 100) -> list[dict[str, Any]]:
        raw_items = self._client.lrange(DLQ_KEY, -limit, -1)
        entries: list[dict[str, Any]] = []
        for raw in raw_items:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                entries.append(payload)
        return list(reversed(entries))

    def find_for_document(self, document_id: UUID) -> dict[str, Any] | None:
        document_key = str(document_id)
        for entry in self.list_entries(limit=1000):
            if entry.get("document_id") == document_key:
                return entry
        return None

    def depth(self) -> int:
        return int(self._client.llen(DLQ_KEY))

    def clear(self) -> None:
        self._client.delete(DLQ_KEY)
