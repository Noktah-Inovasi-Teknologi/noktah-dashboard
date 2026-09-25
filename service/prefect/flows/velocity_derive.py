"""
Velocity Derive — Scheduled Derivation Flow (feature 006)

Turns the append-only observation history into rate of change: per-metric
deltas, per-day rates normalized by MEASURED elapsed time, and a scale-free
plateau/acceleration verdict.

Runs SEPARATELY from collection, on its own schedule. Nothing here touches a
platform, so a derivation failure cannot affect a harvest and a blocked profile
cannot prevent derivation over observations already recorded. It also means
thresholds can be recalibrated and everything re-derived without re-listing a
single profile.

Zero model calls. This is arithmetic.

WHAT TO EXPECT ON DAY ONE: nothing. Velocity needs two observations of the same
item, and the second arrives with the next monthly harvest. A store full of
`legacy_only` immediately after deployment is the correct state, not a failure —
see `velocity_status` for the per-item reason.

Usage (inside the container):
    docker exec prefect python flows/velocity_derive.py --validate-only
    docker exec prefect python flows/velocity_derive.py
    docker exec prefect python flows/velocity_derive.py --platform instagram
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import asyncpg
from prefect import flow
from prefect.logging import get_run_logger

try:
    from ..db import maybe_transaction
    from ..tasks.velocity_tasks import _derive_velocity_impl
except ImportError:
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    from db import maybe_transaction
    from tasks.velocity_tasks import _derive_velocity_impl

try:
    from .common.alerts import alert_hooks
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from common.alerts import alert_hooks

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


@flow(name="velocity-derive", description="Derive engagement velocity from stored metric observations", **alert_hooks())
async def velocity_derive_flow(
    platform: Optional[str] = None,
    content_ids: Optional[List[str]] = None,
    since: Optional[str] = None,
    validate_only: bool = False,
) -> Dict[str, Any]:
    """
    Args:
        platform: Restrict to 'instagram' or 'tiktok'. None = all.
        content_ids: Restrict to specific items. None = all.
        since: Only items with an observation at or after this ISO date.
        validate_only: Report what would be derived without committing. The
            derivation still runs inside a rolled-back transaction, so the
            counts are what would actually land (FR-027).

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
            async with maybe_transaction(conn, validate_only):
                summary = await _derive_velocity_impl(
                    conn, platform=platform, content_ids=content_ids, since=since
                )
        finally:
            await conn.close()

        run_logger.info(
            f"velocity-derive: items={summary['items_considered']} "
            f"derived={summary['intervals_derived']} updated={summary['intervals_updated']} "
            f"withheld_too_short={summary['intervals_withheld_too_short']} "
            f"accelerations={summary['accelerations_detected']} "
            f"insufficient_observations={summary['items_insufficient_observations']} "
            f"config={summary['config_version']}/{summary['detection_metric']}"
            + (" [DRY RUN — nothing written]" if validate_only else "")
        )
        if summary["items_considered"] and not summary["intervals_derived"] and not summary["intervals_updated"]:
            # Said out loud so an operator does not read a working system as a
            # broken one. Until an item is observed twice there is nothing to
            # derive, and that is expected for a full cycle after deployment.
            run_logger.warning(
                f"No intervals derived: all {summary['items_insufficient_observations']} item(s) "
                "still have a single observation. Velocity needs a second capture, which arrives "
                "with the next monthly harvest."
            )
    except Exception as e:
        run_logger.error(f"velocity-derive failed: {e}")
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
    parser.add_argument("--platform", choices=["instagram", "tiktok"], help="Restrict to one platform")
    parser.add_argument("--content-id", action="append", dest="content_ids", help="Restrict to specific item(s); repeatable")
    parser.add_argument("--since", help="Only items observed on/after this ISO date")
    parser.add_argument("--validate-only", action="store_true", dest="validate_only",
                        help="Report what would be derived without writing")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    result = asyncio.run(velocity_derive_flow(
        platform=args.platform,
        content_ids=args.content_ids,
        since=args.since,
        validate_only=args.validate_only,
    ))

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    output_path = os.path.join(OUTPUT_DIR, f"velocity_derive_{timestamp}.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(json.dumps(result["summary"], indent=2, default=str))
    print(f"Full result saved to {output_path}")
    # The flow never raises (constitution I), so without this a caught failure
    # would still exit 0 and a scripted invocation could not tell.
    sys.exit(1 if result.get("error") else 0)
