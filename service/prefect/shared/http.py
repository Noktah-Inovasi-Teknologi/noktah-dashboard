"""
Resilient async HTTP wrapper.

Runs blocking ``requests`` calls in a thread (so they don't block the event
loop), applies an optional ``AsyncRateLimiter``, and classifies responses into
retryable vs terminal errors -- surfacing status code, headers and body so the
caller (and the logs) can see exactly why a request failed.
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

import requests

from .rate_limit import AsyncRateLimiter
from .retry import (
    RetryableError,
    TerminalError,
    is_retryable,
    retry_after_seconds,
)

logger = logging.getLogger(__name__)


@dataclass
class HttpResponse:
    """Lightweight view of an HTTP response."""
    status_code: int
    headers: Mapping[str, str]
    text: str

    def json(self) -> Any:
        import json
        return json.loads(self.text) if self.text else None


async def async_request(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    data: Optional[Any] = None,
    json_body: Optional[Any] = None,
    timeout: float = 120.0,
    limiter: Optional[AsyncRateLimiter] = None,
    raise_for_status: bool = True,
) -> HttpResponse:
    """
    Perform a rate-limited HTTP request off the event loop.

    On non-2xx responses (when ``raise_for_status`` is True) this raises
    ``RetryableError`` for transient statuses (429/5xx, with any ``Retry-After``
    attached) and ``TerminalError`` otherwise. It also feeds throttling/success
    signals back into ``limiter`` so subsequent calls self-pace.

    Args:
        method: HTTP method (e.g. "POST").
        url: Target URL.
        headers: Request headers.
        data: Raw body (e.g. a JSON string).
        json_body: JSON-serializable body (sets Content-Type via requests).
        timeout: Per-request timeout in seconds.
        limiter: Optional shared rate limiter for this API.
        raise_for_status: If True, raise typed errors on non-2xx.

    Returns:
        An ``HttpResponse`` for 2xx (or any status if ``raise_for_status`` is False).
    """
    if limiter is not None:
        await limiter.acquire()

    def _do_request() -> requests.Response:
        return requests.request(
            method=method,
            url=url,
            headers=headers,
            data=data,
            json=json_body,
            timeout=timeout,
        )

    response = await asyncio.to_thread(_do_request)
    result = HttpResponse(
        status_code=response.status_code,
        headers=dict(response.headers),
        text=response.text,
    )

    if 200 <= result.status_code < 300:
        if limiter is not None:
            limiter.on_success()
        return result

    if not raise_for_status:
        return result

    if is_retryable(result.status_code):
        retry_after = retry_after_seconds(result.headers)
        if limiter is not None:
            limiter.on_throttled(retry_after)
        # Log the throttle/transient failure with full context.
        logger.warning(
            f"{method} {url} -> {result.status_code} (retryable); "
            f"Retry-After={retry_after}; body={result.text[:500]}"
        )
        raise RetryableError(
            f"{method} {url} failed with status {result.status_code}",
            retry_after=retry_after,
            status_code=result.status_code,
        )

    logger.error(
        f"{method} {url} -> {result.status_code} (terminal); body={result.text[:1000]}"
    )
    raise TerminalError(
        f"{method} {url} failed with status {result.status_code}: {result.text[:500]}",
        status_code=result.status_code,
    )
