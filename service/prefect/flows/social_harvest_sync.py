"""
Social Content Harvest — Sheet → DB Sync Flow

Reconciles the `harvested_signals` database with the **Account Social Harvest
sheets**, which are the source of truth for reviewer-edited fields (currently the
manual `advertisement` TRUE/FALSE flag). Staff edit the per-account
"{username} - Social Harvest" workbook; this flow:

  1. reads every account sheet (all quarter tabs) → the canonical advertisement value
     per (platform, content_id),
  2. writes those values into `harvested_signals` (so songbird sees them),
  3. corrects the per-run "Social Harvest Detail" sheets to match the account
     sheets, so the whole system stays consistent.

The account sheet is canonical: if a detail sheet disagrees, it is overwritten to
match. Only rows that actually differ are written. Never raises (constitution I).

Usage (inside the container):
    docker exec prefect python flows/social_harvest_sync.py
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

from prefect import flow
from prefect.logging import get_run_logger

try:
    from ..tasks.social_tasks import (
        social_detail_sync_advertisement,
        social_signal_apply_advertisement,
        social_signal_distinct_accounts,
        _parse_advert,
    )
    from ..tasks.google_tasks import (
        drive_folder_ensure,
        google_filter_files_in_folder,
        google_read_sheet_data,
        google_read_spreadsheet_info,
        sheets_spreadsheet_ensure,
    )
    from .common.social_harvest import DETAIL_FOLDER_NAME, _platform_display
except ImportError:
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    sys.path.append(os.path.dirname(__file__))
    from tasks.social_tasks import (
        social_detail_sync_advertisement,
        social_signal_apply_advertisement,
        social_signal_distinct_accounts,
        _parse_advert,
    )
    from tasks.google_tasks import (
        drive_folder_ensure,
        google_filter_files_in_folder,
        google_read_sheet_data,
        google_read_spreadsheet_info,
        sheets_spreadsheet_ensure,
    )
    from common.social_harvest import DETAIL_FOLDER_NAME, _platform_display

try:
    from .common.alerts import alert_hooks
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from common.alerts import alert_hooks

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


@flow(name="social-harvest-sync", description="Reconcile harvested_signals + detail sheets from the canonical account sheets", **alert_hooks())
async def social_harvest_sync_flow(credentials_block_name: str = "google-creds"):
    """
    Sync reviewer-edited fields (advertisement) from the canonical account sheets
    into the DB, then correct the detail sheets to match.

    Returns:
        Dict with start_time, end_time, data, summary, and optional error.
    """
    run_logger = get_run_logger()
    start_time = datetime.now(timezone.utc)
    summary: Dict[str, Any] = {
        "accounts_scanned": 0, "items_read": 0, "db_rows_changed": 0,
        "detail_sheets_scanned": 0, "detail_cells_corrected": 0,
    }
    data: List[Dict[str, Any]] = []
    error = None
    end_time = start_time

    try:
        parent_id = os.environ["HARVEST_DRIVE_PARENT_ID"]
        accounts = await social_signal_distinct_accounts()

        # 1) Read canonical advertisement values from every account sheet.
        canonical: Dict[str, bool] = {}          # "platform|content_id" -> bool
        updates: List[List[Any]] = []            # [platform, content_id, bool]
        for acct in accounts:
            platform, username = acct["platform"], acct["profile_key"]
            platform_folder = await drive_folder_ensure(_platform_display(platform), parent_id, credentials_block_name=credentials_block_name)
            account_folder = await drive_folder_ensure(username, platform_folder, credentials_block_name=credentials_block_name)
            sheet_id = await sheets_spreadsheet_ensure(f"{username} - Social Harvest", account_folder, credentials_block_name=credentials_block_name)
            info = await google_read_spreadsheet_info(sheet_id, credentials_block_name)
            for tab in info.get("sheets", []):
                res = await google_read_sheet_data(sheet_id, tab["title"], credentials_block_name=credentials_block_name)
                for row in res.get("data", []):
                    content_id = str(row.get("content_id") or "").strip()
                    if not content_id or "advertisement" not in row:
                        continue
                    adv = _parse_advert(row.get("advertisement"))
                    canonical[f"{platform}|{content_id}"] = adv
                    updates.append([platform, content_id, adv])
            summary["accounts_scanned"] += 1
        summary["items_read"] = len(updates)
        run_logger.info(f"Read {len(updates)} items across {summary['accounts_scanned']} account sheet(s)")

        # 2) Apply to the DB (only changed rows are written).
        summary["db_rows_changed"] = await social_signal_apply_advertisement(updates)
        run_logger.info(f"DB advertisement rows changed: {summary['db_rows_changed']}")

        # 3) Correct the per-run detail sheets to match the account sheets.
        detail_folder_id = await drive_folder_ensure(DETAIL_FOLDER_NAME, parent_id, credentials_block_name=credentials_block_name)
        detail_files = await google_filter_files_in_folder(detail_folder_id, " - ", credentials_block_name=credentials_block_name, max_results=500)
        for f in detail_files:
            corrected = await social_detail_sync_advertisement(f["id"], canonical, credentials_block_name=credentials_block_name)
            summary["detail_sheets_scanned"] += 1
            summary["detail_cells_corrected"] += corrected
            if corrected:
                data.append({"detail_sheet_id": f["id"], "cells_corrected": corrected})
        run_logger.info(
            f"Corrected {summary['detail_cells_corrected']} cell(s) across "
            f"{summary['detail_sheets_scanned']} detail sheet(s)"
        )

    except Exception as e:
        run_logger.error(f"Sync failed: {e}")
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


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    result = asyncio.run(social_harvest_sync_flow())
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    output_path = os.path.join(OUTPUT_DIR, f"social_harvest_sync_{timestamp}.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print(json.dumps(result["summary"], indent=2))
    print(f"Full result saved to {output_path}")
    sys.exit(1 if result.get("error") else 0)
