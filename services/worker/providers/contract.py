from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from services.worker.extraction.schemas import Invoice


class GenerationParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, gt=0)


class ExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_text: str = Field(min_length=1)
    correlation_id: UUID | None = None
    document_id: UUID | None = None
    generation_parameters: GenerationParameters = Field(
        default_factory=GenerationParameters,
    )


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice: Invoice
    provider: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    model_version: str | None = None
    prompt_version: str = Field(min_length=1)
    generation_parameters: GenerationParameters
    extracted_at: datetime
    correlation_id: UUID | None = None
    document_id: UUID | None = None
    latency_ms: float = Field(ge=0.0)
    token_usage: int | None = Field(default=None, ge=0)
    fallback_reason: str | None = None
    application_version: str | None = None


class LLMProvider(Protocol):
    def extract(self, request: ExtractionRequest) -> ExtractionResult: ...
