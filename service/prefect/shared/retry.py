"""
Retry / backoff primitives shared across workflows.

Provides:
- typed ``RetryableError`` / ``TerminalError`` so callers can distinguish
  transient failures (worth retrying) from permanent ones (fail fast);
- HTTP status classification and ``Retry-After`` parsing;
- an exponential-backoff-with-jitter async runner.

This is the fix for the previous "swallow the error into a dict" behavior that
defeated Prefect's retries and caused bulk Jira creation to silently stop.
"""
import asyncio
import logging
import random
from typing import Awaitable, Callable, Mapping, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# HTTP statuses that indicate a transient condition worth retrying.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class RetryableError(Exception):
    """A transient failure that should be retried (optionally after ``retry_after``)."""

    def __init__(self, message: str, retry_after: Optional[float] = None,
                 status_code: Optional[int] = None):
        super().__init__(message)
        self.retry_after = retry_after
        self.status_code = status_code


class TerminalError(Exception):
    """A permanent failure that should not be retried."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


def is_retryable(status_code: int) -> bool:
    """Return True if an HTTP status code represents a transient, retryable error."""
    return status_code in RETRYABLE_STATUS_CODES


def retry_after_seconds(headers: Mapping[str, str]) -> Optional[float]:
    """
    Parse a ``Retry-After`` header value (delta-seconds form) into a float.

    Jira Cloud returns ``Retry-After`` in seconds on 429/503. HTTP-date form is
    not handled (Jira uses seconds); returns None when absent or unparseable.

    Args:
        headers: Response headers (case-insensitive mapping preferred).

    Returns:
        Seconds to wait, or None.
    """
    if not headers:
        return None

    # Case-insensitive lookup without assuming a specific mapping type.
    value = None
    for key in ("Retry-After", "retry-after", "RETRY-AFTER"):
        if key in headers:
            value = headers[key]
            break
    if value is None:
        try:
            value = headers.get("Retry-After")  # type: ignore[assignment]
        except AttributeError:
            value = None
    if value is None:
        return None

    try:
        seconds = float(value)
        return seconds if seconds >= 0 else None
    except (TypeError, ValueError):
        return None


def backoff_delay(attempt: int, base: float = 1.0, cap: float = 60.0,
                  jitter: bool = True) -> float:
    """
    Compute an exponential backoff delay for a given attempt (0-indexed).

    Args:
        attempt: Retry attempt number (0 for the first retry).
        base: Base delay in seconds.
        cap: Maximum delay in seconds.
        jitter: If True, apply full jitter (random in [0, computed]).

    Returns:
        Delay in seconds.
    """
    raw = min(cap, base * (2 ** attempt))
    if jitter:
        return random.uniform(0, raw)
    return raw


async def run_with_backoff(
    fn: Callable[[], Awaitable[T]],
    max_attempts: int = 4,
    base_delay: float = 1.0,
    cap: float = 60.0,
) -> T:
    """
    Run an async ``fn``, retrying on ``RetryableError`` with exponential backoff.

    ``TerminalError`` and unexpected exceptions propagate immediately. When a
    ``RetryableError`` carries ``retry_after``, that server-provided delay is
    honored instead of the computed backoff.

    Args:
        fn: Zero-arg async callable to execute.
        max_attempts: Total attempts (initial try + retries).
        base_delay: Base backoff delay in seconds.
        cap: Maximum backoff delay in seconds.

    Returns:
        The result of ``fn``.

    Raises:
        The last ``RetryableError`` if all attempts are exhausted, or any
        ``TerminalError`` / unexpected exception raised by ``fn``.
    """
    last_error: Optional[RetryableError] = None
    for attempt in range(max_attempts):
        try:
            return await fn()
        except RetryableError as exc:
            last_error = exc
            if attempt == max_attempts - 1:
                break
            delay = exc.retry_after if exc.retry_after is not None else backoff_delay(
                attempt, base=base_delay, cap=cap
            )
            logger.warning(
                f"Retryable error (status={exc.status_code}) on attempt "
                f"{attempt + 1}/{max_attempts}; retrying in {delay:.1f}s: {exc}"
            )
            await asyncio.sleep(delay)

    assert last_error is not None
    logger.error(f"Exhausted {max_attempts} attempts; giving up: {last_error}")
    raise last_error
