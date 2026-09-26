"""
Harvest attempt tasks (spec 009, research R12).

One `harvest_attempts` row per account per harvest-registry run: what the Hub's Harvest
page shows as "last harvested", and what makes the next run a first harvest (90 days) or
a monthly one (31 days). Written by Prefect, like `runs` and `harvested_signals`, not
through hub-api.

Raises on failure so Prefect retries (constitution I); the flow treats a failed write as
non-fatal, since the harvest it describes already happened.
"""
import os
from datetime import datetime
from typing import Optional

import asyncpg
from prefect import task

try:
    from ..db import db_pool
except ImportError:
    import sys
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    from db import db_pool

OUTCOMES = ("collected", "nothing_new", "blocked", "not_found", "failed", "skipped")
TRIGGERS = ("monthly", "manual")


async def _db_pool() -> asyncpg.Pool:
    return await db_pool("SPINE_DB_URL")


@task(name="harvest.attempt.record", retries=2, retry_delay_seconds=10)
async def harvest_attempt_record(
    account_id: str,
    trigger: str,
    window_days: int,
    started_at: datetime,
    ended_at: Optional[datetime],
    outcome: str,
    posts_collected: int = 0,
    reason: Optional[str] = None,
    flow_run_id: Optional[str] = None,
) -> int:
    """Append one attempt; returns its id. The outcome and trigger vocabularies are the
    table's CHECK constraints (migration 015), checked here first for a clearer error."""
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown harvest outcome {outcome!r}")
    if trigger not in TRIGGERS:
        raise ValueError(f"unknown harvest trigger {trigger!r}")
    pool = await _db_pool()
    try:
        async with pool.acquire() as conn:
            return await conn.fetchval(
                """INSERT INTO harvest_attempts (account_id, flow_run_id, trigger, window_days, started_at,
                                                 ended_at, outcome, posts_collected, reason)
                   VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING id""",
                account_id, flow_run_id, trigger, window_days, started_at, ended_at, outcome,
                posts_collected, (reason or None) and reason[:500],
            )
    finally:
        await pool.close()
