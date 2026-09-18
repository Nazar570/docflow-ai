from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from services.worker.extraction.schemas import Invoice


class DecisionType(StrEnum):
    AUTO_APPROVE = "auto_approve"
    NEEDS_REVIEW = "needs_review"
    HUMAN_APPROVE = "human_approve"
    REJECTED = "rejected"


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)


class WorkflowDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: DecisionType
    reason: str = Field(min_length=1)
    actor: str = Field(default="system", min_length=1)
    validation: ValidationResult


class RuleContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    supported_currencies: list[str]
    auto_approval_confidence_threshold: float
    auto_approval_limit: Decimal
    totals_tolerance: Decimal
    supplier_exists: bool
    purchase_order_exists: bool
    is_duplicate: bool


def validate_invoice(invoice: Invoice, context: RuleContext) -> ValidationResult:
    issues: list[ValidationIssue] = []
    if invoice.total_amount < 0:
        issues.append(
            ValidationIssue(
                code="negative_total",
                message="Invoice total amount must be non-negative",
            ),
        )
    for index, item in enumerate(invoice.line_items):
        if item.quantity < 0 or item.unit_price < 0 or item.line_total < 0:
            issues.append(
                ValidationIssue(
                    code="negative_line_amount",
                    message=f"Line item {index} contains a negative monetary value",
                ),
            )
    if invoice.currency not in context.supported_currencies:
        issues.append(
            ValidationIssue(
                code="unsupported_currency",
                message=f"Currency {invoice.currency} is not supported",
            ),
        )
    if invoice.line_items:
        line_sum = sum((item.line_total for item in invoice.line_items), Decimal("0"))
        if abs(line_sum - invoice.total_amount) > context.totals_tolerance:
            issues.append(
                ValidationIssue(
                    code="total_mismatch",
                    message="Invoice total does not match line item totals",
                ),
            )
    if invoice.overall_confidence < context.auto_approval_confidence_threshold:
        issues.append(
            ValidationIssue(
                code="low_confidence",
                message="Overall extraction confidence is below threshold",
            ),
        )
    if invoice.total_amount > context.auto_approval_limit:
        issues.append(
            ValidationIssue(
                code="auto_approval_limit_exceeded",
                message="Invoice total exceeds auto-approval limit",
            ),
        )
    if not context.supplier_exists:
        issues.append(
            ValidationIssue(
                code="unknown_supplier",
                message="Supplier was not found in ERP",
            ),
        )
    if invoice.purchase_order_id and not context.purchase_order_exists:
        issues.append(
            ValidationIssue(
                code="unknown_purchase_order",
                message="Purchase order was not found in ERP",
            ),
        )
    if context.is_duplicate:
        issues.append(
            ValidationIssue(
                code="duplicate_invoice",
                message="Duplicate invoice detected for supplier and invoice number",
            ),
        )
    return ValidationResult(is_valid=len(issues) == 0, issues=issues)


def decide_workflow(
    invoice: Invoice,
    context: RuleContext,
) -> WorkflowDecision:
    validation = validate_invoice(invoice, context)
    if validation.is_valid:
        return WorkflowDecision(
            decision=DecisionType.AUTO_APPROVE,
            reason="All validation rules passed",
            actor="system",
            validation=validation,
        )
    review_codes = {
        "low_confidence",
        "total_mismatch",
        "unsupported_currency",
        "unknown_supplier",
        "unknown_purchase_order",
        "duplicate_invoice",
        "auto_approval_limit_exceeded",
        "negative_total",
        "negative_line_amount",
    }
    issue_codes = {issue.code for issue in validation.issues}
    if issue_codes & review_codes:
        reasons = "; ".join(issue.message for issue in validation.issues)
        return WorkflowDecision(
            decision=DecisionType.NEEDS_REVIEW,
            reason=reasons,
            actor="system",
            validation=validation,
        )
    return WorkflowDecision(
        decision=DecisionType.NEEDS_REVIEW,
        reason="Validation failed",
        actor="system",
        validation=validation,
    )
