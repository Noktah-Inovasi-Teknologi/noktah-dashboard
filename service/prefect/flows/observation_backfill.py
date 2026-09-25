"""
Observation Backfill — One-Time Legacy Seeding Flow (feature 006)

Carries every pre-feature `harvested_signals` row forward as a single
`metric_observations` row marked `provenance='legacy'`, so the observation
record is complete: no captured value lives only in the old single-row table.

This is what makes the derived history status trustworthy. "Has no observation
history" becomes a statement about observations (FR-021a) rather than about
which table someone remembered to check.

It does NOT reconstruct a past. The seeded observation carries the row's real
`harvested_at` — a moment the value genuinely was recorded — and nothing
earlier is estimated or interpolated. What that timestamp is not is a capture
near publication, which is exactly why the row is marked `legacy`: a velocity
interval anchored on it rests on a value observed at an unknown point in the
item's life, and FR-023a requires that stay visible.

Standalone and unscheduled, mirroring `flows/spine_backfill.py`. Deliberately
NOT a parameter on `velocity-derive`: that flow is scheduled monthly, and a
one-shot operation riding on it would fire every month if its default were ever
wrong.

Idempotent — re-running seeds only items that still have no observation at all.

Usage (inside the container):
    docker exec prefect python flows/observation_backfill.py --validate-only
    docker exec prefect python flows/observation_backfill.py
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
    from ..tasks.velocity_tasks import _observation_backfill_impl
except ImportError:
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    from db import maybe_transaction
    from tasks.velocity_tasks import _observation_backfill_impl

try:
    from .common.alerts import alert_hooks
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from common.alerts import alert_hooks

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


@flow(name="observation-backfill", description="One-time seeding of pre-feature signal rows as legacy observations", **alert_hooks())
async def observation_backfill_flow(dry_run: bool = False) -> Dict[str, Any]:
    """
    Args:
        dry_run: Report what would be seeded without writing. The inserts still
            execute inside a rolled-back transaction, so the reported count is
            what would actually land — not merely how many candidates matched.

    Returns:
        Dict with start_time, end_time, data, summary, and optional error.
        Never raises (constitution I).
    """
    run_logger = get_run_logger()
    start_time = datetime.now(timezone.utc)
    summary: Dict[str, Any] = {}
    data: List[Dict[str, Any]] = []
    error = None
    end_time = start_time

    dsn = os.environ.get("SPINE_DB_URL") or os.environ["HARVEST_DB_URL"]
    try:
        conn = await asyncpg.connect(dsn)
        try:
            async with maybe_transaction(conn, dry_run):
                summary = await _observation_backfill_impl(conn)
        finally:
            await conn.close()

        run_logger.info(
            f"observation-backfill: candidates={summary['candidates']} "
            f"seeded={summary['seeded']} "
            f"skipped_no_metrics={summary['skipped_no_metrics']}"
            + (" [DRY RUN — nothing written]" if dry_run else "")
        )
        # The corpus grows with every harvest, so this count is a moving target
        # and is reported rather than asserted against a literal (research R1).
        if summary["skipped_no_metrics"]:
            run_logger.warning(
                f"{summary['skipped_no_metrics']} signal rows carry no metric at all and were "
                "skipped — they have nothing to observe (field_availability says why)"
            )
    except Exception as e:
        run_logger.error(f"observation-backfill failed: {e}")
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
    parser.add_argument("--validate-only", action="store_true", dest="dry_run",
                        help="Report what would be seeded without writing")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    result = asyncio.run(observation_backfill_flow(dry_run=args.dry_run))

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    output_path = os.path.join(OUTPUT_DIR, f"observation_backfill_{timestamp}.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(json.dumps(result["summary"], indent=2, default=str))
    print(f"Full result saved to {output_path}")
    # The flow never raises (constitution I), so without this a caught failure
    # would still exit 0 and a scripted invocation could not tell.
    sys.exit(1 if result.get("error") else 0)
