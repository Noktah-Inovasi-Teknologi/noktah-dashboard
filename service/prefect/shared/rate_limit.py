"""
Async rate limiting with adaptive backoff-on-429.

One ``AsyncRateLimiter`` should be created per external API (e.g. one for Jira,
one for Google) and shared by the tasks that call that API. It enforces a
minimum interval between calls and, when the server signals throttling (429),
temporarily widens that interval so bursts across many clients don't trip the
limit -- this is what lets the content-plan pipeline scale past ~10 clients.
"""
import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class AsyncRateLimiter:
    """
    Minimum-interval rate limiter with multiplicative backoff on throttling.

    Usage::

        limiter = AsyncRateLimiter(min_interval=1.0)
        await limiter.acquire()          # blocks until it's safe to call
        try:
            ... call the API ...
            limiter.on_success()
        except RateLimited:
            limiter.on_throttled(retry_after)

    The limiter is safe for concurrent use within a single event loop.
    """

    def __init__(
        self,
        min_interval: float = 1.0,
        max_interval: float = 30.0,
        backoff_factor: float = 2.0,
        recovery_factor: float = 0.5,
    ):
        """
        Args:
            min_interval: Baseline minimum seconds between calls.
            max_interval: Upper bound the interval can grow to under throttling.
            backoff_factor: Multiplier applied to the current interval on a 429.
            recovery_factor: Fraction of the *extra* interval shed on each success
                (0.5 = halve the gap back toward ``min_interval``).
        """
        self._base_interval = min_interval
        self._min_interval = min_interval
        self._max_interval = max_interval
        self._backoff_factor = backoff_factor
        self._recovery_factor = recovery_factor
        self._current_interval = min_interval
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    @property
    def current_interval(self) -> float:
        """The interval currently enforced between calls (grows under throttling)."""
        return self._current_interval

    async def acquire(self) -> None:
        """Block until the configured interval has elapsed since the last call."""
        async with self._lock:
            now = time.monotonic()
            wait = self._current_interval - (now - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()

    def on_success(self) -> None:
        """Report a successful call; gradually relaxes the interval toward baseline."""
        if self._current_interval > self._base_interval:
            extra = self._current_interval - self._base_interval
            self._current_interval = max(
                self._base_interval,
                self._base_interval + extra * self._recovery_factor,
            )

    def on_throttled(self, retry_after: Optional[float] = None) -> None:
        """
        Report a throttling response (HTTP 429/503); widens the interval.

        Args:
            retry_after: Server-provided ``Retry-After`` seconds, if any. When
                present and larger than the computed interval, it takes priority.
        """
        widened = min(self._max_interval, self._current_interval * self._backoff_factor)
        if retry_after is not None:
            widened = min(self._max_interval, max(widened, retry_after))
        self._current_interval = widened
        logger.warning(
            f"Rate limiter throttled; interval widened to {self._current_interval:.1f}s"
        )
