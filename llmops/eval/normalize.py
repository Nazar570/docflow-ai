from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any


def normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    return " ".join(value.strip().casefold().split())


def normalize_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def normalize_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def normalize_currency(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip().upper()


def normalize_line_item(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "description": normalize_text(str(item.get("description", ""))),
        "quantity": normalize_decimal(item.get("quantity")),
        "unit_price": normalize_decimal(item.get("unit_price")),
        "line_total": normalize_decimal(item.get("line_total")),
    }


def normalize_line_items(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not items:
        return []
    normalized = [normalize_line_item(item) for item in items]
    return sorted(
        normalized,
        key=lambda item: (
            item["description"] or "",
            str(item["quantity"]),
            str(item["unit_price"]),
            str(item["line_total"]),
        ),
    )


def values_equal(field: str, expected: Any, actual: Any) -> bool:
    if field in {
        "supplier_name",
        "invoice_number",
        "supplier_id",
        "purchase_order_id",
    }:
        return normalize_text(
            None if expected is None else str(expected),
        ) == normalize_text(None if actual is None else str(actual))
    if field == "currency":
        return normalize_currency(
            None if expected is None else str(expected),
        ) == normalize_currency(None if actual is None else str(actual))
    if field in {"total_amount", "overall_confidence"}:
        left = normalize_decimal(expected)
        right = normalize_decimal(actual)
        if left is None or right is None:
            return left is None and right is None
        if field == "overall_confidence":
            return abs(left - right) <= Decimal("0.000001")
        return left == right
    if field == "invoice_date":
        return normalize_date(expected) == normalize_date(actual)
    if field == "line_items":
        expected_items = expected if isinstance(expected, list) else []
        actual_items = actual if isinstance(actual, list) else []
        return normalize_line_items(expected_items) == normalize_line_items(
            actual_items,
        )
    return bool(expected == actual)
