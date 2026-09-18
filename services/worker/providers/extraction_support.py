from pydantic import ValidationError

from services.worker.extraction.prompt_versions import (
    PROMPT_VERSION,
    load_prompt_template,
)
from services.worker.extraction.schemas import Invoice, parse_invoice_payload
from services.worker.providers.errors import ProviderOutputError
from services.worker.providers.json_parse import parse_json_object


def build_extraction_messages(
    document_text: str,
    *,
    validation_feedback: str | None = None,
) -> list[dict[str, str]]:
    system_prompt = load_prompt_template()
    user_content = (
        f"Prompt version: {PROMPT_VERSION}\n"
        "Extract invoice fields as a single JSON object matching the invoice schema.\n"
        f"Document text:\n{document_text}"
    )
    if validation_feedback is not None:
        user_content = (
            f"{user_content}\n\n"
            "Previous JSON failed validation. Return corrected JSON only.\n"
            f"Validation errors:\n{validation_feedback}"
        )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def validate_invoice_text(raw_text: str) -> Invoice:
    try:
        payload = parse_json_object(raw_text)
        return parse_invoice_payload(payload)
    except (ValueError, ValidationError) as exc:
        raise ProviderOutputError(
            "Provider output failed invoice schema validation",
        ) from exc


def format_validation_feedback(exc: ProviderOutputError) -> str:
    cause = exc.__cause__
    if cause is None:
        return exc.message
    return str(cause)
