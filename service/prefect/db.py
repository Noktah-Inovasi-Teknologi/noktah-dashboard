"""
Shared PostgreSQL helpers for the Prefect service.

Every task module needs the same two things — a connection pool built from the
harvest DSN, and (for flows with a `--validate-only` mode) a transaction that
rolls itself back. Both lived in duplicate across five modules before this;
keeping them here means pool sizing and DSN resolution have one definition.

A top-level module rather than something under `tasks/`, matching `hashmap.py`:
flows import it too, and it is not itself a Prefect task.
"""
import os
from contextlib import asynccontextmanager

import asyncpg

# Every consumer reads the same database; the override env vars exist so a
# single flow can be pointed at a rehearsal clone (see script/db_rehearsal.sh)
# without touching the rest of the service.
DEFAULT_DSN_ENV = "HARVEST_DB_URL"


async def db_pool(override_env: str | None = None) -> asyncpg.Pool:
    """
    Open a pool against the harvest database.

    Args:
        override_env: Optional env var checked first, so one consumer can be
            redirected (e.g. `SPINE_DB_URL`, `SONGBIRD_DB_URL`) while the rest
            keep using `HARVEST_DB_URL`.
    """
    dsn = (os.environ.get(override_env) if override_env else None) or os.environ[DEFAULT_DSN_ENV]
    return await asyncpg.create_pool(dsn, min_size=1, max_size=5)


class _DryRunRollback(Exception):
    """Internal sentinel — never escapes `maybe_transaction`."""


@asynccontextmanager
async def maybe_transaction(conn: asyncpg.Connection, dry_run: bool):
    """
    Run the body in a transaction, rolling it back when `dry_run` is set.

    Lets a flow share one code path between its real and `--validate-only`
    modes: the body still executes (so its report counts what *would* change)
    but nothing is committed. Without this, each flow re-implements the same
    sentinel-exception dance.
    """
    try:
        async with conn.transaction():
            yield
            if dry_run:
                raise _DryRunRollback()
    except _DryRunRollback:
        pass
