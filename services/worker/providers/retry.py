import time
from collections.abc import Callable


def call_with_retries[T](
    operation: Callable[[], T],
    *,
    max_retries: int,
    base_delay_seconds: float = 0.25,
) -> T:
    from services.worker.providers.errors import RecoverableProviderError

    attempt = 0
    while True:
        try:
            return operation()
        except RecoverableProviderError:
            if attempt >= max_retries:
                raise
            delay = base_delay_seconds * (2**attempt)
            time.sleep(delay)
            attempt += 1
