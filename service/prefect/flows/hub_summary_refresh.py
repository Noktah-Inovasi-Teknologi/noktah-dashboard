"""
Hub Summary Refresh: hourly. Asks hub-api to refresh every Client's Ringkasan that is
stale, at most once a day per Client (spec 008, G-27). hub-api does the work and
enforces the monthly AI cap; this flow only schedules it and alerts on failure.

Usage (inside the container):
    docker exec prefect python flows/hub_summary_refresh.py
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


@flow(name="hub-summary-refresh", description="Refresh stale Client Card summaries in Noktah Hub", **alert_hooks("noktah"))
async def hub_summary_refresh_flow() -> Dict[str, Any]:
    log = get_run_logger()
    start = datetime.now(timezone.utc)
    summary: Dict[str, Any] = {}
    error = None
    try:
        summary = hub_internal_call("summaries/refresh")
        log.info(f"summaries: {summary}")
        # Per-Client reasons ride in summary["failures"], which the alert hook lists.
        for name, reason in (summary.get("failures") or {}).items():
            log.warning(f"{name}: {reason}")
    except Exception as e:  # noqa: BLE001 - flows report, never raise
        error = f"{type(e).__name__}: {e}"
        log.error(error)
    return {"start_time": start.isoformat(), "end_time": datetime.now(timezone.utc).isoformat(),
            "data": [], "summary": summary, "error": error}


if __name__ == "__main__":
    result = asyncio.run(hub_summary_refresh_flow())
    print(result["summary"], "| error:", result["error"])
