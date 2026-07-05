"""
Generic batching helpers.

Used to respect external API per-request limits (e.g. Jira Cloud's bulk-create
cap of <=50 issues) without each caller re-implementing slicing logic.
"""
import asyncio
from typing import Any, Awaitable, Callable, Iterable, List, TypeVar

T = TypeVar("T")
R = TypeVar("R")


def chunked(items: Iterable[T], size: int) -> List[List[T]]:
    """
    Split ``items`` into consecutive chunks of at most ``size`` elements.

    Args:
        items: Any iterable.
        size: Maximum chunk size (must be >= 1).

    Returns:
        List of chunks. An empty input yields an empty list.

    Raises:
        ValueError: If ``size`` < 1.
    """
    if size < 1:
        raise ValueError("chunk size must be >= 1")

    materialized = list(items)
    return [materialized[i:i + size] for i in range(0, len(materialized), size)]


async def run_in_batches(
    items: Iterable[T],
    size: int,
    fn: Callable[[List[T]], Awaitable[R]],
    delay_seconds: float = 0.0,
) -> List[R]:
    """
    Apply an async ``fn`` to each chunk of ``items``, optionally sleeping between chunks.

    Args:
        items: Items to process.
        size: Maximum items per batch.
        fn: Async callable invoked once per batch with the batch list.
        delay_seconds: Delay inserted *between* batches (not after the last).

    Returns:
        List of per-batch results in order.
    """
    batches = chunked(items, size)
    results: List[R] = []
    for index, batch in enumerate(batches):
        results.append(await fn(batch))
        if delay_seconds > 0 and index < len(batches) - 1:
            await asyncio.sleep(delay_seconds)
    return results
