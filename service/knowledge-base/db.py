"""PostgreSQL connection pool for the Client Knowledge Base MCP server."""
import logging
import os
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


def _dsn() -> str:
    """Build the Postgres DSN from environment variables.

    Prefers KB_DATABASE_URL (set in docker-compose); falls back to composing
    from the individual POSTGRES_* vars for local/dev use, matching the
    project convention of no secrets in code.
    """
    dsn = os.getenv("KB_DATABASE_URL")
    if dsn:
        return dsn

    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    host = os.getenv("POSTGRES_HOST", "postgres")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = os.getenv("POSTGRES_DB")

    if not all([user, password, database]):
        raise RuntimeError(
            "Database configuration missing: set KB_DATABASE_URL or "
            "POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB environment variables"
        )

    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


async def get_pool() -> asyncpg.Pool:
    """Return the shared connection pool, creating it on first use."""
    global _pool
    if _pool is None:
        logger.info("Creating PostgreSQL connection pool")
        _pool = await asyncpg.create_pool(dsn=_dsn(), min_size=1, max_size=10)
    return _pool


async def close_pool() -> None:
    """Close the shared connection pool, if open."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("PostgreSQL connection pool closed")
