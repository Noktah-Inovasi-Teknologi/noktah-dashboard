"""
Run-record tasks (feature 004-relational-spine, User Story 4).

Gives every collection or generation execution a stable identifier that
outlives it (FR-019), so a later feature can group observations/briefs by the
run that produced them. Structure only in this feature — nothing populates
`briefs` yet (S-06).

Tasks raise on failure so Prefect's retry mechanism can handle transient DB
errors (constitution I). Callers treat run-record failures as best-effort
(wrapped in try/except at the call site, per T049/T051) — a run record is
metadata about a harvest or generation, never a precondition for one.
"""
import json
import logging
import os
from typing import Any, Dict, Optional

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


def _current_flow_run_name() -> Optional[str]:
    """Prefect's auto-generated flow-run name (e.g. 'graceful-rook'), or None
    outside a flow run context. Mirrors _resolve_harvest_name's fallback."""
    try:
        from prefect.runtime import flow_run

        return flow_run.name or None
    except Exception:
        return None


def _current_flow_run_id() -> Optional[str]:
    try:
        from prefect.runtime import flow_run

        return str(flow_run.id) if flow_run.id else None
    except Exception:
        return None


@task(name="run.record.start", retries=2, retry_delay_seconds=10)
async def run_record_start(kind: str, flow_name: str, client_id: Optional[str] = None) -> str:
    """
    Create a run record and return its id.

    Args:
        kind: "collection" or "generation".
        flow_name: e.g. "social-harvest-window", "songbird-generate".
        client_id: the client this run is for, where applicable (null for
            multi-client or unattributed runs, e.g. a multi-profile harvest).
    """
    pool = await _db_pool()
    try:
        async with pool.acquire() as conn:
            run_id = await conn.fetchval(
                """
                INSERT INTO runs (kind, flow_name, flow_run_name, flow_run_id, client_id, status)
                VALUES ($1, $2, $3, $4, $5, 'running')
                RETURNING id
                """,
                kind, flow_name, _current_flow_run_name(), _current_flow_run_id(), client_id,
            )
            return str(run_id)
    finally:
        await pool.close()


@task(name="run.record.finish", retries=2, retry_delay_seconds=10)
async def run_record_finish(run_id: str, status: str, summary: Optional[Dict[str, Any]] = None) -> None:
    """Close a run record. `status` is typically 'completed' or 'failed'."""
    pool = await _db_pool()
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE runs SET ended_at = now(), status = $2, summary = $3::jsonb WHERE id = $1",
                run_id, status, json.dumps(summary, default=str) if summary is not None else None,
            )
    finally:
        await pool.close()
