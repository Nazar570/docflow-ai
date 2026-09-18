from pathlib import Path

PROMPT_VERSION = "invoice_extraction_v1"

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / f"{PROMPT_VERSION}.txt"


def load_prompt_template() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")
