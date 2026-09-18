from enum import StrEnum


class ProviderErrorClass(StrEnum):
    RATE_LIMIT = "rate_limit"
    QUOTA_EXHAUSTED = "quota_exhausted"
    TIMEOUT = "timeout"
    TRANSIENT = "transient"
    AUTH = "auth"
    INVALID_REQUEST = "invalid_request"
    OUTPUT_INVALID = "output_invalid"
    UNAVAILABLE = "unavailable"


class ProviderError(Exception):
    def __init__(
        self,
        message: str,
        *,
        error_class: ProviderErrorClass,
        provider: str,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_class = error_class
        self.provider = provider


class RecoverableProviderError(ProviderError):
    pass


class NonRecoverableProviderError(ProviderError):
    pass


class ProviderOutputError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


RECOVERABLE_ERROR_CLASSES = {
    ProviderErrorClass.RATE_LIMIT,
    ProviderErrorClass.QUOTA_EXHAUSTED,
    ProviderErrorClass.TIMEOUT,
    ProviderErrorClass.TRANSIENT,
}
