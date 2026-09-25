"""
sheet-header-backfill — bring existing harvest tabs to the current column layout.

Feature 005 appends a trailing `shares` column to the per-account quarterly tabs.
`ensure_tab` historically wrote a header only when CREATING a tab, so every tab
that already existed would have stayed one column short forever — rows written
one cell wider than the header describes.

**Scope: per-account quarterly tabs only.** Per-run detail sheets are immutable
historical artifacts and are deliberately left alone — see the comment in the
flow body for why extending them would relabel a populated column.

This is a ONE-OFF migration, not a scheduled flow. Run it once after deploying
the layout change, so exactly one column layout exists (FR-016a) rather than two
shapes coexisting indefinitely.

Safety properties, in order of importance:

  * Touches ONLY row 1. Reviewer-entered data — notably the `advertisement`
    flags staff set by hand — is never read or written. A layout migration must
    not be able to disturb it.
  * A tab whose existing columns DISAGREE with the expected layout is skipped
    and reported, never overwritten. A mismatch means the tab is not the shape
    we think it is, and rewriting its header would silently relabel columns of
    real data.
  * Idempotent: a tab already carrying the column reports `unchanged`. An
    operator cannot always know which tabs were done, so re-running must be
    harmless.

Run:
    docker exec prefect python flows/sheet_header_backfill.py --validate-only
    docker exec prefect python flows/sheet_header_backfill.py
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict

from prefect import flow, get_run_logger

try:
    from .common.social_harvest import ACCOUNT_HEADER, _platform_display
    from ..tasks.google_tasks import (
        drive_folder_ensure,
        google_read_spreadsheet_info,
        sheets_ensure_header,
        sheets_spreadsheet_ensure,
    )
    from ..tasks.social_tasks import social_signal_distinct_accounts
except ImportError:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from flows.common.social_harvest import ACCOUNT_HEADER, _platform_display
    from tasks.google_tasks import (
        drive_folder_ensure,
        google_read_spreadsheet_info,
        sheets_ensure_header,
        sheets_spreadsheet_ensure,
    )
    from tasks.social_tasks import social_signal_distinct_accounts

try:
    from .common.alerts import alert_hooks
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from common.alerts import alert_hooks


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@flow(name="sheet-header-backfill", **alert_hooks())
async def sheet_header_backfill(
    validate_only: bool = False,
    credentials_block_name: str = "google-creds",
) -> Dict[str, Any]:
    """Extend every existing harvest tab's header to the current layout.

    Returns the standard result dict (constitution I) and never raises.
    """
    run_logger = get_run_logger()
    result: Dict[str, Any] = {
        "start_time": _now(),
        "end_time": None,
        "data": [],
        "summary": {
            "tabs_scanned": 0,
            "tabs_updated": 0,
            "tabs_unchanged": 0,
            "tabs_empty": 0,
            "tabs_skipped": 0,
            "skipped_detail": [],
            "validate_only": validate_only,
        },
        "error": None,
    }

    async def _apply(sheet_id: str, tab: str, label: str) -> None:
        result["summary"]["tabs_scanned"] += 1
        if validate_only:
            # Report what would be touched without writing. The read below is
            # done by the task itself in the real path; here we only record the
            # intent, so --validate-only issues no writes at all.
            result["data"].append({"sheet": label, "tab": tab, "action": "would_check"})
            return
        outcome = await sheets_ensure_header(
            sheet_id, tab, ACCOUNT_HEADER, credentials_block_name=credentials_block_name
        )
        result["data"].append({"sheet": label, "tab": tab, "action": outcome})
        if outcome == "extended":
            result["summary"]["tabs_updated"] += 1
        elif outcome == "unchanged":
            result["summary"]["tabs_unchanged"] += 1
        elif outcome == "empty":
            # A tab with no header row at all — a scratch/default tab. Benign;
            # counted separately so it never inflates the mismatch signal.
            result["summary"]["tabs_empty"] += 1
        else:
            result["summary"]["tabs_skipped"] += 1
            result["summary"]["skipped_detail"].append(
                {"sheet": label, "tab": tab,
                 "reason": "existing header does not match the expected layout; "
                           "left untouched rather than risk relabelling real data"}
            )
            run_logger.warning(f"{label}/{tab}: skipped — header mismatch, not overwritten")

    try:
        parent_id = os.environ["HARVEST_DRIVE_PARENT_ID"]

        # 1) Per-account quarterly tabs.
        # There are at most two platform folders, but resolving one costs an
        # OAuth token refresh plus a Drive list — so cache them rather than
        # paying 2N round-trips to learn two ids (as run_harvest does with
        # its format-folder cache).
        platform_folders: Dict[str, str] = {}
        for acct in await social_signal_distinct_accounts():
            platform, username = acct["platform"], acct["profile_key"]
            if platform not in platform_folders:
                platform_folders[platform] = await drive_folder_ensure(
                    _platform_display(platform), parent_id,
                    credentials_block_name=credentials_block_name)
            platform_folder = platform_folders[platform]
            account_folder = await drive_folder_ensure(
                username, platform_folder, credentials_block_name=credentials_block_name)
            title = f"{username} - Social Harvest"
            sheet_id = await sheets_spreadsheet_ensure(
                title, account_folder, credentials_block_name=credentials_block_name)
            info = await google_read_spreadsheet_info(sheet_id, credentials_block_name)
            for tab in info.get("sheets", []):
                await _apply(sheet_id, tab["title"], title)

        # 2) Per-run detail sheets are DELIBERATELY NOT backfilled.
        #
        # Each harvest run creates its own detail sheet and never writes to a
        # previous one, so they are immutable historical artifacts. Two reasons
        # not to touch them:
        #
        #   * Rewriting a past run's header would claim a `shares` column its
        #     rows do not have — fabricating structure over real records.
        #   * `shares` is NOT a trailing column in DETAIL_HEADER. That header is
        #     derived as ACCOUNT_HEADER + ["account_folder_id"], so appending to
        #     ACCOUNT_HEADER *inserts* mid-layout here, landing `shares` exactly
        #     where old sheets hold `account_folder_id`. Extending them would
        #     relabel a populated column.
        #
        # Verified live 2026-08-02: all 53 existing detail sheets reported
        # `mismatch` for precisely that reason, and were correctly left alone.
        # `social-harvest-sync` reads them by header NAME (FR-016b), so old and
        # new layouts both keep working with no migration at all.
        run_logger.info(
            "per-run detail sheets skipped by design — immutable historical artifacts; "
            "new runs create sheets with the current layout"
        )

        s = result["summary"]
        run_logger.info(
            f"scanned={s['tabs_scanned']} extended={s['tabs_updated']} "
            f"unchanged={s['tabs_unchanged']} empty={s['tabs_empty']} skipped={s['tabs_skipped']}"
        )
        if s["tabs_skipped"]:
            run_logger.warning(
                f"{s['tabs_skipped']} tab(s) skipped on header mismatch — inspect before re-running; "
                f"name-based reads keep these safe in the meantime (FR-016b)"
            )
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
        run_logger.error(f"header backfill failed: {result['error']}")
    finally:
        result["end_time"] = _now()

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill harvest sheet headers to the current layout")
    parser.add_argument("--validate-only", action="store_true",
                        help="Report which tabs would be touched, writing nothing")
    args = parser.parse_args()

    outcome = asyncio.run(sheet_header_backfill(validate_only=args.validate_only))
    print(json.dumps(outcome, indent=2, ensure_ascii=False, default=str))
    sys.exit(1 if outcome.get("error") else 0)
