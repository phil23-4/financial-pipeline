"""Retry utilities with exponential backoff for resilient network operations."""

import time
from collections.abc import Callable
from functools import wraps
from typing import Any

from loguru import logger as log


def retry_with_backoff(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
    retryable_exceptions: tuple[type[Exception], ...] = (
        IOError,
        OSError,
        TimeoutError,
    ),
) -> Callable:
    """Decorator to retry a function with exponential backoff.

    Args:
        max_attempts: Maximum number of retry attempts
        initial_delay: Initial delay in seconds between retries
        max_delay: Maximum delay in seconds between retries
        backoff_factor: Multiplier for exponential backoff
        retryable_exceptions: Tuple of exception types to retry on

    Returns:
        Decorated function that retries on specified exceptions
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            attempt = 0
            delay = initial_delay

            while attempt < max_attempts:
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    attempt += 1
                    if attempt >= max_attempts:
                        log.error(
                            f"Max retries ({max_attempts}) exceeded for {func.__name__}: {e}"
                        )
                        raise

                    log.warning(
                        f"Attempt {attempt}/{max_attempts} failed for {func.__name__}: {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    time.sleep(delay)
                    delay = min(delay * backoff_factor, max_delay)

        return wrapper

    return decorator
