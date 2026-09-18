from pathlib import Path

from services.worker.extraction.pdf_samples import build_pdf_bytes
from services.worker.extraction.pdf_text import extract_text_from_pdf


def test_extract_text_from_pdf_reads_text(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(build_pdf_bytes("Invoice INV-1001 Acme Supplies GmbH"))
    text = extract_text_from_pdf(pdf_path)
    assert "INV-1001" in text
    assert "Acme" in text
