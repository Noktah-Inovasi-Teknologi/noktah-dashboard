"""
Read one Content Plan sheet into the Hub's row shape (spec 009).

Shared by `hub-plan-watch` (which sends the rows to hub-api, where the fingerprint a
Greenlight records is computed) and `hub-jira-create` (which re-reads the same sheet and
recomputes that fingerprint before creating anything). Both MUST read the same way, or the
create flow would see a "changed" plan on every run — so there is one reader, here.

The tab rule is the Jira reader's: `Sheet1` when present, else the first tab
(tasks/content_plan_files.py `pick_plan_tab`). Values are the displayed values
(FORMATTED_VALUE), so a Key cell holding `=HYPERLINK(url, "ESKL-12")` reads as `ESKL-12`.
"""
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

try:
    from ...tasks.content_plan_files import pick_plan_tab
    from ...tasks.content_plan_rows import rows_from_values
    from ...tasks.google_tasks import google_read_sheet_raw, google_read_spreadsheet_info
except ImportError:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from tasks.content_plan_files import pick_plan_tab
    from tasks.content_plan_rows import rows_from_values
    from tasks.google_tasks import google_read_sheet_raw, google_read_spreadsheet_info


def quoted_tab(tab_name: str) -> str:
    """A tab name as an A1 range (quoted, so spaces and punctuation are safe)."""
    return "'" + tab_name.replace("'", "''") + "'"


async def read_plan_rows(
    spreadsheet_id: str,
    tab_name: Optional[str] = None,
    credentials_block_name: str = "google-creds",
) -> Tuple[str, List[str], List[Dict[str, Any]]]:
    """(tab name, header, rows) of a plan. `tab_name` given → that tab; else the plan tab rule.

    Raises when the sheet cannot be read or has no tab; the caller decides what that means
    (an `unreadable` plan for the watcher, a refused plan for the create flow).
    """
    if not tab_name:
        info = await google_read_spreadsheet_info(spreadsheet_id, credentials_block_name)
        tab_name = pick_plan_tab([s.get("title") for s in info.get("sheets", [])])
        if not tab_name:
            raise ValueError("spreadsheet has no tabs")
    raw = await google_read_sheet_raw(spreadsheet_id=spreadsheet_id, sheet_name=quoted_tab(tab_name),
                                      credentials_block_name=credentials_block_name)
    header, rows = rows_from_values(raw.get("values") or [])
    return tab_name, header, rows
