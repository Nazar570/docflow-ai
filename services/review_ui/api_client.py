from typing import Any
from uuid import UUID

import httpx


class ReviewApiClient:
    def __init__(self, *, base_url: str, timeout_seconds: float = 30.0) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout_seconds)

    def close(self) -> None:
        self._client.close()

    def list_reviews(self) -> dict[str, Any]:
        response = self._client.get("/reviews")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("Expected JSON object from /reviews")
        return payload

    def get_review(self, document_id: UUID | str) -> dict[str, Any]:
        response = self._client.get(f"/reviews/{document_id}")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("Expected JSON object from review detail")
        return payload

    def get_document_status(self, document_id: UUID | str) -> dict[str, Any]:
        response = self._client.get(f"/documents/{document_id}")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("Expected JSON object from document status")
        return payload

    def approve(
        self,
        document_id: UUID | str,
        *,
        actor: str,
        reason: str,
        edited_invoice: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        payload: dict[str, Any] = {"actor": actor, "reason": reason}
        if edited_invoice is not None:
            payload["edited_invoice"] = edited_invoice
        response = self._client.post(
            f"/reviews/{document_id}/approve",
            json=payload,
        )
        body: dict[str, Any]
        try:
            parsed = response.json()
            body = parsed if isinstance(parsed, dict) else {"detail": parsed}
        except Exception:
            body = {"detail": response.text}
        return response.status_code, body

    def reject(
        self,
        document_id: UUID | str,
        *,
        actor: str,
        reason: str,
    ) -> tuple[int, dict[str, Any]]:
        response = self._client.post(
            f"/reviews/{document_id}/reject",
            json={"actor": actor, "reason": reason},
        )
        body: dict[str, Any]
        try:
            parsed = response.json()
            body = parsed if isinstance(parsed, dict) else {"detail": parsed}
        except Exception:
            body = {"detail": response.text}
        return response.status_code, body

    def source_url(self, document_id: UUID | str) -> str:
        return f"{self._client.base_url}/documents/{document_id}/source"
