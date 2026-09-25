"""
Hub Sheet Sync: keeps the Clients and Hashmaps tabs as read-only copies of the
Noktah Hub Registry (spec 008, FR-016..FR-018, research R5). Every 5 minutes it
copies changed cells; on Monday 06:00 it runs in check mode, rewriting any cell
someone edited in the sheet and reporting what it overwrote.

hub-api does the work. A failed call is retried by the task, then the flow alerts #noktah-otomasi;
the sync position isn't advanced, so the next run retries the same
changes (never silently skipped, G-7).

Usage (inside the container):
    docker exec prefect python flows/hub_sheet_sync.py                  # copy changes
    docker exec prefect python flows/hub_sheet_sync.py --check          # full check
    docker exec prefect python flows/hub_sheet_sync.py --validate-only  # dry run: list cells, write none
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


@flow(name="hub-sheet-sync", description="Copy the Noktah Hub Registry into the Clients/Hashmaps sheet tabs",
      **alert_hooks("noktah"))
async def hub_sheet_sync_flow(check: bool = False, validate_only: bool = False) -> Dict[str, Any]:
    log = get_run_logger()
    start = datetime.now(timezone.utc)
    summary: Dict[str, Any] = {}
    error = None
    try:
        summary = hub_internal_call("sheet-sync", params={"check": str(check).lower(),
                                                          "dry_run": str(validate_only).lower()})
        log.info(f"sheet sync: {summary.get('status')}, {summary.get('written', 0)} cell(s) written")
        for item in summary.get("overwritten", []):
            log.warning(f"overwrote {item['cell']} ({item['what']}): sheet had {item['sheet']!r}, "
                        f"Registry says {item['registry']!r}")
    except Exception as e:  # noqa: BLE001 - flows report, never raise
        error = f"{type(e).__name__}: {e}"
        log.error(error)
    return {"start_time": start.isoformat(), "end_time": datetime.now(timezone.utc).isoformat(),
            "data": summary.get("overwritten") or summary.get("would_write") or [],
            "summary": {k: v for k, v in summary.items() if k not in ("overwritten", "would_write")}, "error": error}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="diff every managed cell, not only changes")
    parser.add_argument("--validate-only", action="store_true", help="dry run: list the cells, write nothing")
    args = parser.parse_args()
    result = asyncio.run(hub_sheet_sync_flow(check=args.check, validate_only=args.validate_only))
    print(json.dumps({"summary": result["summary"], "cells": result["data"]}, ensure_ascii=False, indent=2))
    print("error:", result["error"])
