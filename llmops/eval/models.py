from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RuleContextOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supplier_exists: bool = True
    purchase_order_exists: bool = True
    is_duplicate: bool = False
    supported_currencies: list[str] | None = None
    auto_approval_confidence_threshold: float | None = None
    auto_approval_limit: Decimal | None = None
    totals_tolerance: Decimal | None = None


class GoldenCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    scenario_tags: list[str] = Field(default_factory=list)
    document_text: str = Field(min_length=1)
    fake_scenario: str = Field(min_length=1)
    expected_invoice: dict[str, Any] | None = None
    expected_decision: (
        Literal["auto_approve", "needs_review", "rejected", "provider_failure"] | None
    ) = None
    expected_final_state: (
        Literal["submitted", "needs_review", "rejected", "failed"] | None
    ) = None
    schema_applicable: bool = True
    field_comparison_applicable: bool = True
    decision_applicable: bool = True
    expected_error_class: str | None = None
    rule_overrides: RuleContextOverrides = Field(default_factory=RuleContextOverrides)


class DatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_version: str = Field(min_length=1)
    description: str = Field(min_length=1)
    limitations: str = Field(min_length=1)
    case_files: list[str] = Field(min_length=1)
