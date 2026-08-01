"""
Spine Backfill — One-Time Row Linking Flow (feature 004-relational-spine)

Links the existing harvested_signals / harvested_items / knowledge_records rows
to the new accounts/clients tables. Run once, after roster-sync has populated
the roster from the current spreadsheets — any ledger handle roster-sync did
not cover (an unattributable handle, an opaque platform identifier) gets an
account with no client role here, per FR-008, rather than being dropped.

Idempotent: re-running only touches rows still missing a link, so it is safe
to run again after a roster-sync catches up an account this run left
unattributed.

Usage (inside the container):
    docker exec prefect python flows/spine_backfill.py
    docker exec prefect python flows/spine_backfill.py --validate-only
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

import asyncpg
from prefect import flow
from prefect.logging import get_run_logger

try:
    from ..db import maybe_transaction
    from ..tasks.spine_tasks import _knowledge_link_impl, _link_table_impl
except ImportError:
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    from db import maybe_transaction
    from tasks.spine_tasks import _knowledge_link_impl, _link_table_impl

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# A real handle on this roster is short (the longest, "klinikspesialislangsa",
# is 21 chars); the one opaque case measured in research R2 — a TikTok
# sec_uid — is 68 chars. This threshold classifies by that gap, not by trying
# to parse either platform's internal id format.
OPAQUE_IDENTIFIER_LENGTH_THRESHOLD = 24


def _identifier_kind(profile_key: str) -> str:
    return "opaque_id" if len(profile_key) > OPAQUE_IDENTIFIER_LENGTH_THRESHOLD else "handle"


async def backfill_spine(conn: asyncpg.Connection, dry_run: bool = False) -> Dict[str, Any]:
    """
    Create no-role accounts for any ledger handle not already covered by
    account_handles, then link harvested_signals / harvested_items /
    knowledge_records to their accounts/client.

    Returns {"summary": {...}}. Never raises for ordinary data shapes — an
    unmatched handle is exactly what this flow exists to handle.
    """
    summary = {
        "accounts_created": 0,
        "signals_linked": 0,
        "items_linked": 0,
        "knowledge_linked": 0,
        "knowledge_unresolved_current": 0,
        "unattributed_handles": [],
    }

    # Distinct (platform, profile_key) across both harvest tables not yet
    # covered by any account_handles row — the unregistered handles.
    uncovered = await conn.fetch(
        """
        SELECT DISTINCT platform, profile_key FROM (
            SELECT platform, profile_key FROM harvested_signals
            UNION
            SELECT platform, profile_key FROM harvested_items
        ) t
        WHERE NOT EXISTS (
            SELECT 1 FROM account_handles ah
            WHERE ah.platform = t.platform AND ah.handle_key = lower(t.profile_key)
        )
        """
    )
    for row in uncovered:
        platform, profile_key = row["platform"], row["profile_key"]
        kind = _identifier_kind(profile_key)
        summary["accounts_created"] += 1
        summary["unattributed_handles"].append({"platform": platform, "handle": profile_key, "identifier_kind": kind})
        if dry_run:
            continue
        account_id = await conn.fetchval("INSERT INTO accounts (platform) VALUES ($1) RETURNING id", platform)
        await conn.execute(
            """
            INSERT INTO account_handles (account_id, platform, handle_key, handle_text, identifier_kind, is_current)
            VALUES ($1, $2, $3, $4, $5, true)
            """,
            account_id, platform, profile_key.lower(), profile_key, kind,
        )

    summary["signals_linked"] = await _link_table_impl(conn, "harvested_signals", dry_run=dry_run)
    summary["items_linked"] = await _link_table_impl(conn, "harvested_items", dry_run=dry_run)
    knowledge = await _knowledge_link_impl(conn, dry_run=dry_run)
    summary["knowledge_linked"] = knowledge["linked"]
    summary["knowledge_unresolved_current"] = knowledge["unresolved_current"]

    return {"summary": summary}


@flow(name="spine-backfill", description="One-time link of existing harvest/knowledge rows to accounts/clients")
async def spine_backfill_flow(dry_run: bool = False) -> Dict[str, Any]:
    """
    Args:
        dry_run: Report what would be created/linked without writing (T062 —
            this mutates every row in both harvest tables in one pass).

    Returns:
        Dict with start_time, end_time, data, summary, and optional error.
        Never raises.
    """
    run_logger = get_run_logger()
    start_time = datetime.now(timezone.utc)
    summary: Dict[str, Any] = {}
    data: List[Dict[str, Any]] = []
    error = None

    dsn = os.environ.get("SPINE_DB_URL") or os.environ["HARVEST_DB_URL"]
    try:
        conn = await asyncpg.connect(dsn)
        try:
            async with maybe_transaction(conn, dry_run):
                result = await backfill_spine(conn, dry_run=dry_run)
        finally:
            await conn.close()

        summary = result["summary"]
        run_logger.info(
            f"spine-backfill: accounts_created={summary['accounts_created']} "
            f"signals_linked={summary['signals_linked']} items_linked={summary['items_linked']} "
            f"knowledge_linked={summary['knowledge_linked']} "
            f"knowledge_unresolved={summary['knowledge_unresolved_current']}"
            + (" [DRY RUN — nothing written]" if dry_run else "")
        )
    except Exception as e:
        run_logger.error(f"spine-backfill failed: {e}")
        error = str(e)
    finally:
        end_time = datetime.now(timezone.utc)

    return {
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "data": data,
        "summary": summary,
        "error": error,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--validate-only", action="store_true", dest="dry_run", help="Report what would change without writing")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    result = asyncio.run(spine_backfill_flow(dry_run=args.dry_run))

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    output_path = os.path.join(OUTPUT_DIR, f"spine_backfill_{timestamp}.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(json.dumps(result["summary"], indent=2, default=str))
    print(f"Full result saved to {output_path}")
    sys.exit(1 if result.get("error") else 0)
