"""
Hub Notes Process: turns the old AnythingLLM notes (knowledge_records) into Intakes
in Noktah Hub, so their content is reviewed and accepted like any Intake (spec 008,
FR-040, research R7). Manual, never scheduled.

--dry-run is the DEFAULT: zero model calls, the notes in scope, how they match, and
the projected spend. The real run stops at the monthly AI cap and resumes next time;
it is idempotent by note, so re-running skips what's done.

Usage (inside the container):
    docker exec prefect python flows/hub_notes_process.py                  # dry run
    docker exec prefect python flows/hub_notes_process.py --apply          # real run
    docker exec prefect python flows/hub_notes_process.py --apply --limit 10
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from prefect import flow
from prefect.logging import get_run_logger

try:
    from ..tasks.hub_tasks import hub_internal_call
except ImportError:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tasks.hub_tasks import hub_internal_call

try:
    from .common.alerts import alert_hooks
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from common.alerts import alert_hooks


@flow(name="hub-notes-process", description="Turn the old AnythingLLM notes into Noktah Hub Intakes for review",
      **alert_hooks("noktah"))
async def hub_notes_process_flow(dry_run: bool = True, limit: Optional[int] = None) -> Dict[str, Any]:
    log = get_run_logger()
    start = datetime.now(timezone.utc)
    report: Dict[str, Any] = {}
    error = None
    try:
        params: Dict[str, Any] = {"dry_run": str(dry_run).lower()}
        if limit:
            params["limit"] = limit
        report = hub_internal_call("notes/process", params=params, timeout=1800.0)
        if dry_run:
            log.info(f"dry run: {report['in_scope']} note(s) in scope, {report['matched']} matched, "
                     f"{report['unmatched']} unmatched; projected ${report['projected_usd']} ({report['projected_basis']})")
        else:
            log.info(f"processed: {report}")
            if report.get("paused_by_cap"):
                log.warning("stopped at the monthly AI cap; the next run resumes where this one stopped")
            if report.get("failed"):
                error = f"{report['failed']} note(s) failed at the AI step"
    except Exception as e:  # noqa: BLE001 - flows report, never raise
        error = f"{type(e).__name__}: {e}"
        log.error(error)
    return {"start_time": start.isoformat(), "end_time": datetime.now(timezone.utc).isoformat(),
            "data": [], "summary": report, "error": error}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="make the model calls (default: dry run)")
    parser.add_argument("--limit", type=int, default=None, help="process at most this many notes")
    args = parser.parse_args()
    result = asyncio.run(hub_notes_process_flow(dry_run=not args.apply, limit=args.limit))
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print("error:", result["error"])
