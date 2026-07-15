"""
Songbird content-generation tasks (feature 003-songbird-content-generation).

Single-responsibility data-access tasks that gather the "hit" signals songbird
generates from: the client's own knowledge (knowledge_records) and the ranked
top-performing harvested posts (harvested_signals). Both raise on failure so
Prefect's retry mechanism handles transient DB errors (constitution I).

Generation itself (prompt assembly + OpenRouter) lives in the engine
(flows/common/songbird.py) using tasks/openrouter_tasks.py; delivery reuses the
existing Google Sheet/Drive tasks in tasks/google_tasks.py.
"""
import logging
import os
import re
from typing import Any, Dict, List

import asyncpg
from prefect import task

try:
    from ..blocks.google_credentials import GoogleCredentials
except ImportError:
    # For running as standalone script
    import sys
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    from blocks.google_credentials import GoogleCredentials

logger = logging.getLogger(__name__)

# Per-client content configuration lives in the operator-maintained Clients
# worksheet (the same workbook the content-plan → Jira flow reads). Overridable
# via env so the exact spreadsheet/tab/column can be tuned without code changes.
CLIENTS_SPREADSHEET_ID = os.environ.get(
    "SONGBIRD_CLIENTS_SPREADSHEET_ID", "1-aV46TIn4m_zs3vtCNeS_Bvl3Tt-tgg09uuG_NqgNNY"
)
CLIENTS_TAB = os.environ.get("SONGBIRD_CLIENTS_TAB", "Clients")
CLIENTS_NAME_COLUMN = os.environ.get("SONGBIRD_CLIENTS_NAME_COLUMN", "Client")
QUANTITY_COLUMN = os.environ.get("SONGBIRD_QUANTITY_COLUMN", "Jumlah Konten")


def _normalize_key(name: str) -> str:
    """Best-effort client_key candidate: lowercased, collapsed whitespace."""
    return re.sub(r"\s+", " ", name.strip().lower())


async def _db_pool() -> asyncpg.Pool:
    # Reuse the harvest DSN by default (same Postgres database); a dedicated
    # SONGBIRD_DB_URL may override it.
    dsn = os.environ.get("SONGBIRD_DB_URL") or os.environ["HARVEST_DB_URL"]
    return await asyncpg.create_pool(dsn, min_size=1, max_size=5)


@task(name="songbird.client.context", retries=2, retry_delay_seconds=30)
async def songbird_client_context(client_name: str, limit: int = 40) -> Dict[str, Any]:
    """
    Fetch the client's *current* knowledge records to ground generation.

    Matches on client_key/client_name with trigram fuzziness (the KB may store a
    slightly different casing/spelling than the flow's client param). Only
    non-superseded rows are returned (the current truth per subject).

    Returns:
        {"client_name": <resolved or input>, "records": [{"subject", "information"}, ...]}
    """
    key = _normalize_key(client_name)
    pool = await _db_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT client_name, subject, information
                FROM knowledge_records
                WHERE superseded_by IS NULL
                  AND (client_key = $1 OR client_name ILIKE $2 OR similarity(client_key, $1) > 0.3)
                ORDER BY similarity(client_key, $1) DESC, "timestamp" DESC
                LIMIT $3
                """,
                key, f"%{client_name.strip()}%", limit,
            )
    finally:
        await pool.close()

    records = [{"subject": r["subject"], "information": r["information"]} for r in rows]
    resolved = rows[0]["client_name"] if rows else client_name
    logger.info(f"Loaded {len(records)} current knowledge records for '{client_name}' (resolved '{resolved}')")
    return {"client_name": resolved, "records": records}


@task(name="songbird.signal.top-performers", retries=2, retry_delay_seconds=30)
async def songbird_top_performers(
    handles: List[str], limit: int = 10, window_days: int = 180
) -> List[Dict[str, Any]]:
    """
    Rank the top-performing harvested posts for a set of profile handles by
    engagement (likes + comments), returning their metadata + roach analysis.

    Only content published within the last `window_days` days is considered
    (FR-009a) — a rolling window keeps the "hit" signal current; items with an
    unknown published_at are included so signal is never silently dropped. Handles
    are matched case-insensitively against harvested_signals.profile_key (the
    account handle social-harvest stores). Returns [] when nothing has been
    harvested for those handles yet — the engine then falls back to
    client-knowledge + marketing params only.

    Args:
        handles: Profile handles (own and/or competitor) to rank across.
        limit: Max exemplars to return.
        window_days: Rolling recency window in days (default 180, FR-009a).
    """
    if not handles:
        return []
    lowered = [h.strip().lower() for h in handles if h and h.strip()]
    if not lowered:
        return []
    pool = await _db_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT platform, profile_key, content_type, published_at, caption,
                       hashtags, views, likes, comments, subtitle, content_flow, summary,
                       (COALESCE(likes, 0) + COALESCE(comments, 0)) AS engagement
                FROM harvested_signals
                WHERE lower(profile_key) = ANY($1::text[])
                  AND (published_at IS NULL
                       OR published_at >= now() - make_interval(days => $2))
                ORDER BY engagement DESC, published_at DESC NULLS LAST
                LIMIT $3
                """,
                lowered, window_days, limit,
            )
    finally:
        await pool.close()

    performers = [dict(r) for r in rows]
    logger.info(
        f"Ranked {len(performers)} top performers across {len(lowered)} handle(s) "
        f"within {window_days}d window"
    )
    return performers


@task(name="songbird.config.quantity", retries=2, retry_delay_seconds=30)
async def songbird_config_quantity(
    client_name: str, credentials_block_name: str = "google-creds"
) -> int:
    """
    Read the monthly content quantity configured for a client from the Clients
    worksheet (FR-003b). The spreadsheet id, tab, client-name column, and
    quantity column are env-overridable.

    Matches the client row case-insensitively (exact, then contains) on the
    client-name column and coerces the quantity cell to a positive int.

    Raises:
        ValueError: the client row or a valid positive quantity is not found —
            the flow MUST fail fast rather than guess a count (FR-003b).
    """
    google_creds = await GoogleCredentials.load_or_env(credentials_block_name)
    client = google_creds.get_client()
    df = client.to_dataframe(
        spreadsheet_id=CLIENTS_SPREADSHEET_ID, sheet_name=CLIENTS_TAB, header_row=0
    )
    records = df.to_dict("records") if df is not None and not df.empty else []
    if not records:
        raise ValueError(
            f"Clients worksheet '{CLIENTS_TAB}' ({CLIENTS_SPREADSHEET_ID}) is empty or unreadable"
        )
    if CLIENTS_NAME_COLUMN not in records[0] or QUANTITY_COLUMN not in records[0]:
        raise ValueError(
            f"Clients worksheet missing required column(s): expected "
            f"'{CLIENTS_NAME_COLUMN}' and '{QUANTITY_COLUMN}'; found {list(records[0].keys())}. "
            f"Set SONGBIRD_CLIENTS_NAME_COLUMN / SONGBIRD_QUANTITY_COLUMN to match the sheet."
        )

    target = client_name.strip().lower()
    row = next(
        (r for r in records if str(r.get(CLIENTS_NAME_COLUMN, "")).strip().lower() == target),
        None,
    ) or next(
        (r for r in records if target in str(r.get(CLIENTS_NAME_COLUMN, "")).strip().lower()),
        None,
    )
    if row is None:
        raise ValueError(
            f"No row for client '{client_name}' in Clients worksheet column '{CLIENTS_NAME_COLUMN}'"
        )

    raw = row.get(QUANTITY_COLUMN)
    quantity = _coerce_quantity(raw)
    if quantity is None or quantity <= 0:
        raise ValueError(
            f"Client '{client_name}' has no valid positive '{QUANTITY_COLUMN}' quantity (got {raw!r})"
        )
    logger.info(f"Client '{client_name}' configured monthly quantity: {quantity}")
    return quantity


def _coerce_quantity(value: Any) -> Optional[int]:
    """Coerce a quantity cell ('12' / 12 / 12.0 / '12 konten') to a positive int, or None."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else None
