from pathlib import Path

from pypdf import PdfReader


class PdfTextExtractionError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def extract_text_from_pdf(path: Path) -> str:
    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise PdfTextExtractionError("PDF could not be opened") from exc
    parts: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text.strip():
            parts.append(text)
    combined = "\n".join(parts).strip()
    if not combined:
        raise PdfTextExtractionError("PDF contained no extractable text")
    return combined
