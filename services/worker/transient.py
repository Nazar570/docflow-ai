from services.worker.executor.erp_client import ErpClientError
from services.worker.providers.errors import RecoverableProviderError


class TransientWorkerError(Exception):
    def __init__(self, message: str, *, error_class: str) -> None:
        super().__init__(message)
        self.message = message
        self.error_class = error_class


def is_transient_erp_error(error: ErpClientError) -> bool:
    if error.status_code is None:
        return True
    return error.status_code in {408, 425, 429, 500, 502, 503, 504}


def classify_transient_error(exc: BaseException) -> TransientWorkerError | None:
    if isinstance(exc, TransientWorkerError):
        return exc
    if isinstance(exc, RecoverableProviderError):
        return TransientWorkerError(
            exc.message,
            error_class=exc.error_class.value,
        )
    if isinstance(exc, ErpClientError) and is_transient_erp_error(exc):
        return TransientWorkerError(
            exc.message,
            error_class="erp_transient",
        )
    return None
