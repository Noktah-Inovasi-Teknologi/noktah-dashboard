"""One asyncpg pool for the process, opened at startup and closed at shutdown."""
from typing import Optional

import asyncpg

_pool: Optional[asyncpg.Pool] = None


async def open_pool(dsn: str) -> None:
    global _pool
    _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("database pool is not open")
    return _pool
