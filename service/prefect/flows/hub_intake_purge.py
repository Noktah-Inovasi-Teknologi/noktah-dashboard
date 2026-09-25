"""
Hub Intake Purge: daily. Asks hub-api to remove Intake raw material (pasted text,
screenshots, documents) older than 12 months. The quoted excerpts, Proposals and
their outcomes are kept forever (spec 008, FR-036). hub-api does the work; this
flow only schedules it and alerts on failure.

Usage (inside the container):
    docker exec prefect python flows/hub_intake_purge.py
"""
import asyncio
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict

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


@flow(name="hub-intake-purge", description="Remove Noktah Hub Intake raw material older than 12 months",
      **alert_hooks("noktah"))
async def hub_intake_purge_flow() -> Dict[str, Any]:
    log = get_run_logger()
    start = datetime.now(timezone.utc)
    summary: Dict[str, Any] = {}
    error = None
    try:
        summary = hub_internal_call("intakes/purge-raw")
        log.info(f"purged raw material from {summary.get('purged', 0)} intake(s)")
    except Exception as e:  # noqa: BLE001 - flows report, never raise
        error = f"{type(e).__name__}: {e}"
        log.error(error)
    return {"start_time": start.isoformat(), "end_time": datetime.now(timezone.utc).isoformat(),
            "data": [], "summary": summary, "error": error}


if __name__ == "__main__":
    result = asyncio.run(hub_intake_purge_flow())
    print(result["summary"], "| error:", result["error"])
