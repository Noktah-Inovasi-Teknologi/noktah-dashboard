"""
Spine backfill tasks (feature 004-relational-spine).

One-time linking of existing harvested_signals / harvested_items /
knowledge_records rows to the new accounts/clients tables. Like roster_tasks.py,
each public `@task` opens its own pool and delegates to a plain `_*_impl`
function taking an open connection, so flows/spine_backfill.py's engine can
share one connection/transaction across the whole backfill (tested directly
in tests/test_spine_backfill.py without any task-decorator machinery).
"""
import logging
import os
from typing import Dict

import asyncpg
from prefect import task

try:
    from ..db import db_pool
except ImportError:
    import sys
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    from db import db_pool

logger = logging.getLogger(__name__)


async def _db_pool() -> asyncpg.Pool:
    return await db_pool("SPINE_DB_URL")


async def _link_table_impl(conn: asyncpg.Connection, table: str, dry_run: bool = False) -> int:
    """Link `table.account_id` by matching `(platform, lower(profile_key))`
    against `account_handles` (current AND former handles). Returns the count
    that were (or, in dry-run, would be) linked."""
    if dry_run:
        row = await conn.fetchrow(
            f"""
            SELECT count(*) AS n FROM {table} t
            JOIN account_handles ah ON ah.platform = t.platform AND ah.handle_key = lower(t.profile_key)
            WHERE t.account_id IS NULL
            """
        )
        return row["n"]
    result = await conn.execute(
        f"""
        UPDATE {table} t SET account_id = ah.account_id
        FROM account_handles ah
        WHERE ah.platform = t.platform AND ah.handle_key = lower(t.profile_key)
          AND t.account_id IS NULL
        """
    )
    return int(result.split()[-1]) if result.startswith("UPDATE") else 0


@task(name="spine.signal.link", retries=2, retry_delay_seconds=10)
async def spine_signal_link(dry_run: bool = False) -> int:
    pool = await _db_pool()
    try:
        async with pool.acquire() as conn:
            return await _link_table_impl(conn, "harvested_signals", dry_run=dry_run)
    finally:
        await pool.close()


@task(name="spine.item.link", retries=2, retry_delay_seconds=10)
async def spine_item_link(dry_run: bool = False) -> int:
    pool = await _db_pool()
    try:
        async with pool.acquire() as conn:
            return await _link_table_impl(conn, "harvested_items", dry_run=dry_run)
    finally:
        await pool.close()


async def _knowledge_link_impl(conn: asyncpg.Connection, dry_run: bool = False) -> Dict[str, int]:
    """FR-017: link knowledge_records to a client via a matching alias.
    Unresolvable records are retained and reported, never dropped."""
    if dry_run:
        row = await conn.fetchrow(
            """
            SELECT count(*) AS n FROM knowledge_records k
            JOIN client_aliases ca ON ca.alias_key = lower(regexp_replace(btrim(k.client_name), '\\s+', ' ', 'g'))
            WHERE k.client_id IS NULL
            """
        )
        linked = row["n"]
    else:
        result = await conn.execute(
            """
            UPDATE knowledge_records k SET client_id = ca.client_id
            FROM client_aliases ca
            WHERE ca.alias_key = lower(regexp_replace(btrim(k.client_name), '\\s+', ' ', 'g'))
              AND k.client_id IS NULL
            """
        )
        linked = int(result.split()[-1]) if result.startswith("UPDATE") else 0

    # "Unresolved" means no matching alias exists — checked directly rather
    # than via `client_id IS NULL`, which in dry-run mode is trivially true
    # for every row (nothing was written) and would misreport every linkable
    # row as unresolved. Post-write in a real run the two definitions agree.
    unresolved = await conn.fetchval(
        """
        SELECT count(*) FROM knowledge_records k
        WHERE k.superseded_by IS NULL
          AND k.client_id IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM client_aliases ca
              WHERE ca.alias_key = lower(regexp_replace(btrim(k.client_name), '\\s+', ' ', 'g'))
          )
        """
    )
    return {"linked": linked, "unresolved_current": unresolved}


@task(name="spine.knowledge.link", retries=2, retry_delay_seconds=10)
async def spine_knowledge_link(dry_run: bool = False) -> Dict[str, int]:
    pool = await _db_pool()
    try:
        async with pool.acquire() as conn:
            return await _knowledge_link_impl(conn, dry_run=dry_run)
    finally:
        await pool.close()
