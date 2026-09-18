import hashlib


def content_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def build_idempotency_key(*, source_message_id: str, content_hash: str) -> str:
    material = f"{source_message_id}:{content_hash}".encode()
    return hashlib.sha256(material).hexdigest()
