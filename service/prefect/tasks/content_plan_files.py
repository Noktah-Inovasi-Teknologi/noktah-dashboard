"""
Which file IS a client's content plan for a month. One rule, shared by every reader and writer.

Pure functions: no Prefect, no I/O. The callers list the folder and read the tabs.

A plan is the Google Sheet named exactly `Content Plan - {client} - {month}` in the
client's "Content Plan Folder ID" folder. Whitespace and case are forgiven, nothing else is.

Why exact: the old rule also accepted any name that merely CONTAINED the expected one,
and took whichever file Drive listed first. "Salinan Content Plan - Klinik Utama Sumenep -
September 2026" (Google's "Copy of") contains the real plan's name, so a copy could be
read into Jira or written to by songbird. Measured 2026-09-24 across all 22 clients for
September and October: exact matching loses no real plan. The Sumenep copy was the only
file the loose rule added.

Why two exact matches stop instead of guessing: on the same date, Gudang Karung Jumbo
Sidoarjo had two sheets with the identical September name (one created 19 Aug and never
edited, the other edited until 18 Sep). Picking one silently is how the wrong plan becomes
Jira issues. The error names both files so a person deletes or renames one; that takes
a minute, and the alert says exactly which.
"""
import re
from typing import Any, Dict, List, Optional

SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"

# The tab nearly every plan uses. When it is absent the plan is the first tab
# (Klinik Mata Bireuen, Sept 2026: a single tab named `sheet3`).
DEFAULT_PLAN_TAB = "Sheet1"

# What the content-plan -> Jira conversion reads. A tab without these is not a
# content plan, so nothing may be appended to it (this is what stops a
# misdirected write into e.g. the Clients roster).
REQUIRED_PLAN_COLUMNS = ("Tanggal", "Bentuk", "Topik")

INDONESIAN_MONTHS = {
    1: "Januari", 2: "Februari", 3: "Maret", 4: "April", 5: "Mei", 6: "Juni",
    7: "Juli", 8: "Agustus", 9: "September", 10: "Oktober", 11: "November", 12: "Desember",
}


class AmbiguousPlanFileError(ValueError):
    """More than one sheet carries the plan's exact name."""


def plan_month_label(year: int, month: int) -> str:
    """(2026, 10) -> "Oktober 2026", the spelling plan file names use."""
    return f"{INDONESIAN_MONTHS[month]} {year}"


def expected_plan_name(client_name: str, month_label: str) -> str:
    return f"Content Plan - {client_name.strip()} - {month_label.strip()}"


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name or "").strip().casefold()


def pick_plan_file(files: List[Dict[str, Any]], client_name: str, month_label: str) -> Optional[Dict[str, Any]]:
    """The one plan file among a folder listing, or None if there is none.

    Raises AmbiguousPlanFileError when several sheets carry the exact name.
    Files without a mimeType are accepted (some listings omit it); any other type,
    such as a PDF export under the same name, is not a plan.
    """
    wanted = _norm(expected_plan_name(client_name, month_label))
    matches = [
        f for f in files
        if _norm(f.get("name", "")) == wanted
        and f.get("mimeType", SPREADSHEET_MIME) == SPREADSHEET_MIME
    ]
    if len(matches) > 1:
        described = "; ".join(
            f"{f.get('id')}" + (f" (diubah {f['modifiedTime'][:10]})" if f.get("modifiedTime") else "")
            for f in matches
        )
        raise AmbiguousPlanFileError(
            f"Ada {len(matches)} file bernama '{expected_plan_name(client_name, month_label)}': "
            f"{described}. Hapus atau ganti nama salah satunya."
        )
    return matches[0] if matches else None


def pick_plan_tab(tab_titles: List[str]) -> Optional[str]:
    """`Sheet1` when present, else the first tab; the same rule the Jira reader applies."""
    titles = [t for t in tab_titles if t]
    if not titles:
        return None
    return DEFAULT_PLAN_TAB if DEFAULT_PLAN_TAB in titles else titles[0]


def missing_plan_columns(header: List[str]) -> List[str]:
    """Required content-plan columns absent from a header; empty = looks like a plan."""
    present = {str(h).strip() for h in header}
    return [c for c in REQUIRED_PLAN_COLUMNS if c not in present]
