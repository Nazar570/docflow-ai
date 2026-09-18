import json
import re
from typing import Any


def parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise ValueError("Provider returned empty content")
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if match is None:
            raise ValueError("Provider returned non-JSON content") from None
        payload = json.loads(match.group(0))
    if not isinstance(payload, dict):
        raise ValueError("Provider JSON root must be an object")
    return payload
