"""
Hub Registry Import: the ONE-TIME import of the Clients and Hashmaps tabs into
Noktah Hub's Registry (spec 008, FR-014, research R6). Manual, never scheduled.

Validate-only is the DEFAULT: it returns the full difference report and writes
nothing. The real run is a deliberate second step, after a Manager reviewed that
report; it also pauses the roster-sync deployment, since from then on the Hub owns
the roster.

Usage (inside the container):
    docker exec prefect python flows/hub_registry_import.py              # report only
    docker exec prefect python flows/hub_registry_import.py --apply      # the real import
"""
import argparse
import asyncio
import json
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

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


@flow(name="hub-registry-import", description="One-time import of the Clients/Hashmaps tabs into the Noktah Hub Registry",
      **alert_hooks("noktah"))
async def hub_registry_import_flow(validate_only: bool = True) -> Dict[str, Any]:
    log = get_run_logger()
    start = datetime.now(timezone.utc)
    report: Dict[str, Any] = {}
    error = None
    try:
        report = hub_internal_call("registry/import", params={"validate_only": str(validate_only).lower()})
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        path = os.path.join(OUTPUT_DIR, f"hub_registry_import_{start:%Y%m%d_%H%M%S}"
                                        f"{'_validate' if validate_only else ''}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"metadata": {"workflow": "hub-registry-import", "executed_at": start.isoformat(),
                                    "validate_only": validate_only}, "data": report}, f, ensure_ascii=False, indent=2)
        log.info(f"report saved to {path}")
        log.info(f"clients: {len(report['clients']['matched'])} matched, {len(report['clients']['created'])} new; "
                 f"{len(report['differences'])} differences, {len(report['unmatched'])} unmatched, "
                 f"{len(report['skipped'])} skipped; roster-sync: {report.get('roster_sync')}")
        if str(report.get("roster_sync", "")).startswith("pause_failed"):
            error = f"import applied, but roster-sync is still running: {report['roster_sync']}"
    except Exception as e:  # noqa: BLE001 - flows report, never raise
        error = f"{type(e).__name__}: {e}"
        log.error(error)
    summary = {k: (len(v) if isinstance(v, list) else v) for k, v in report.items() if k != "clients"}
    return {"start_time": start.isoformat(), "end_time": datetime.now(timezone.utc).isoformat(),
            "data": report, "summary": summary, "error": error}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="run the REAL import (default: validate only)")
    args = parser.parse_args()
    result = asyncio.run(hub_registry_import_flow(validate_only=not args.apply))
    print(json.dumps(result["data"], ensure_ascii=False, indent=2, default=str))
    print("error:", result["error"])
