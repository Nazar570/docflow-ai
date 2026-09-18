from pathlib import Path
from uuid import UUID


def store_pdf(*, storage_dir: Path, document_id: UUID, content: bytes) -> Path:
    storage_dir.mkdir(parents=True, exist_ok=True)
    path = storage_dir / f"{document_id}.pdf"
    path.write_bytes(content)
    return path
