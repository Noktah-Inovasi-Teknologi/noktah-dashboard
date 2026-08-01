"""
Roster Sync — Sheets -> Datastore Reconciliation Flow

Reads the "Clients" worksheet, the "Hashmaps" worksheet's COMPONENTS and
CLIENT_SOCIAL blocks, and the current distinct client names in
knowledge_records, then reconciles them into clients / client_aliases /
accounts / account_handles / client_account_roles (feature 004-relational-spine).

This is the job FR-022/FR-022a require: it runs on a daily schedule and can
also be triggered on demand, but nothing else waits on it — a collection or
generation run simply uses whatever is registered at the time it runs
(contracts/roster-sync-report.md).

Usage (inside the container):
    docker exec prefect python flows/roster_sync.py
    docker exec prefect python flows/roster_sync.py --validate-only
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
    from ..blocks.google_credentials import GoogleCredentials
    from ..db import maybe_transaction
    from ..hashmap import COMPONENTS, CLIENT_SOCIAL
    from ..tasks.roster_tasks import _db_pool
    from .common.roster import reconcile_roster, SHEET_NAME_COLUMN
except ImportError:
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    sys.path.append(os.path.dirname(__file__))
    from blocks.google_credentials import GoogleCredentials
    from db import maybe_transaction
    from hashmap import COMPONENTS, CLIENT_SOCIAL
    from tasks.roster_tasks import _db_pool
    from common.roster import reconcile_roster, SHEET_NAME_COLUMN

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

CLIENTS_SPREADSHEET_ID = os.environ.get(
    "SONGBIRD_CLIENTS_SPREADSHEET_ID", "1-aV46TIn4m_zs3vtCNeS_Bvl3Tt-tgg09uuG_NqgNNY"
)
CLIENTS_TAB = os.environ.get("SONGBIRD_CLIENTS_TAB", "Clients")


async def _read_clients_sheet(credentials_block_name: str) -> List[Dict[str, Any]]:
    """
    Reads the Clients worksheet. Deliberately raises rather than returning [] on
    any failure — roster-sync infers departures from ABSENCE, so a failed read
    that is silently treated as "no clients" would deactivate the entire roster
    (mirrors hashmap.load_hashmaps(), which already raises rather than handing
    back empty mappings). See contracts/roster-sync-report.md.
    """
    google_creds = await GoogleCredentials.load_or_env(credentials_block_name)
    df = google_creds.get_client().to_dataframe(
        spreadsheet_id=CLIENTS_SPREADSHEET_ID, sheet_name=CLIENTS_TAB, header_row=0
    )
    if df is None or df.empty:
        raise RuntimeError(
            f"Clients worksheet '{CLIENTS_TAB}' ({CLIENTS_SPREADSHEET_ID}) is empty or unreadable — "
            f"refusing to reconcile against an empty read, which would look identical to every "
            f"client having left the roster."
        )
    records = df.to_dict("records")
    if SHEET_NAME_COLUMN not in records[0]:
        raise RuntimeError(f"Clients worksheet has no '{SHEET_NAME_COLUMN}' column; found {list(records[0].keys())}")
    return records


async def _distinct_knowledge_base_client_names() -> List[str]:
    pool = await _db_pool()
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT client_name FROM knowledge_records WHERE superseded_by IS NULL"
            )
            return [r["client_name"] for r in rows]
    finally:
        await pool.close()


@flow(name="roster-sync", description="Reconcile clients/accounts/roles from the Clients + Hashmaps worksheets")
async def roster_sync_flow(dry_run: bool = False, credentials_block_name: str = "google-creds") -> Dict[str, Any]:
    """
    Reconcile the datastore's roster from the operator-maintained spreadsheets.

    Args:
        dry_run: Report what would change without writing anything (T063 —
            the constitution requires a preview for costed batch mutations,
            and deactivation in particular silently stops storage per FR-024a).
        credentials_block_name: Name of the Google credentials block.

    Returns:
        Dict with start_time, end_time, data, summary, and optional error.
        Never raises for ordinary reconciliation problems — a bad Sheets read
        IS raised, deliberately (see _read_clients_sheet).
    """
    run_logger = get_run_logger()
    start_time = datetime.now(timezone.utc)
    summary: Dict[str, Any] = {}
    data: List[Dict[str, Any]] = []
    error = None

    dsn = os.environ.get("SPINE_DB_URL") or os.environ["HARVEST_DB_URL"]
    try:
        clients_sheet_rows = await _read_clients_sheet(credentials_block_name)
        components_block = dict(COMPONENTS)
        client_social_block = dict(CLIENT_SOCIAL)
        knowledge_base_names = await _distinct_knowledge_base_client_names()

        conn = await asyncpg.connect(dsn)
        try:
            async with maybe_transaction(conn, dry_run):
                result = await reconcile_roster(
                    conn, clients_sheet_rows, components_block, client_social_block,
                    knowledge_base_names, dry_run=dry_run,
                )
        finally:
            await conn.close()

        summary = result["summary"]
        run_logger.info(
            f"roster-sync: clients {summary['clients']} accounts {summary['accounts']} "
            f"roles {summary['roles']} unresolved={len(summary['unresolved'])} "
            f"conflicts={len(summary['aliases']['conflicts'])} "
            f"disagreements={len(summary['source_disagreements'])} "
            f"newly_inactive={len(summary['newly_inactive_accounts'])}"
            + (" [DRY RUN — nothing written]" if dry_run else "")
        )
    except Exception as e:
        run_logger.error(f"roster-sync failed: {e}")
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
    parser.add_argument("--credentials-block-name", default="google-creds")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    result = asyncio.run(roster_sync_flow(dry_run=args.dry_run, credentials_block_name=args.credentials_block_name))

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    output_path = os.path.join(OUTPUT_DIR, f"roster_sync_{timestamp}.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(json.dumps(result["summary"], indent=2, default=str))
    print(f"Full result saved to {output_path}")
    sys.exit(1 if result.get("error") else 0)
