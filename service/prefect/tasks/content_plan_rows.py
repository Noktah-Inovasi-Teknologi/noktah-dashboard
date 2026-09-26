"""
Content Plan rows as the Hub sees them (spec 009, research R4/R5). Pure: no Prefect, no I/O.

A plan is read as rows: [{"row_number": 2, "cells": {"Tanggal": "04/10/2026", ...}}], where
`row_number` is the sheet row (the header is row 1, so the first data row is 2) and `cells`
maps each header to the cell's displayed value.

`hub-plan-watch` sends these rows to hub-api, which stores the plan's fingerprint and the
Greenlight records it. `hub-jira-create` re-reads the sheet right before creating issues
and must compute the SAME fingerprint, or it would refuse every plan (or, worse, accept a
changed one). So the fingerprint below is a verbatim copy of hub-api's
`service/api/app/automation/plans.py` (`norm`, `row_fingerprint`, `is_blank`,
`content_rows`, `plan_fingerprint`, FINGERPRINT_COLUMNS, KEY_RE). The two services ship in
different images and cannot import each other; `tests/test_content_plan_rows.py` loads
hub-api's module by path and asserts both give identical output. Change both or neither.
"""
import hashlib
import json
import re
from typing import Any, Dict, List, Optional, Tuple

# ---- verbatim from service/api/app/automation/plans.py --------------------------------------

FINGERPRINT_COLUMNS = (
    "Tanggal", "Waktu", "Bentuk", "Topik", "Creator", "Format", "Purpose/Theme", "Strategic Application",
    "Kebutuhan Personil", "Known Facts", "Shoot Guide", "Visualisasi Konten", "Asset", "Caption",
    "Link Referensi",
)
KEY_COLUMN = "Key"
KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


def norm(value: Any) -> str:
    return " ".join(str(value if value is not None else "").split())


def row_fingerprint(cells: Dict[str, Any]) -> str:
    payload = json.dumps([norm(cells.get(c)) for c in FINGERPRINT_COLUMNS], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def is_blank(cells: Dict[str, Any]) -> bool:
    return not any(norm(cells.get(c)) for c in FINGERPRINT_COLUMNS)


def content_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [r for r in rows if not is_blank(r.get("cells") or {})]


def plan_fingerprint(rows: List[Dict[str, Any]]) -> str:
    parts = [f"{r['row_number']}:{row_fingerprint(r['cells'])}" for r in content_rows(rows)]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def key_of(cells: Dict[str, Any]) -> Optional[str]:
    m = KEY_RE.search(norm(cells.get(KEY_COLUMN)))
    return m.group(1) if m else None

# ---- end of the verbatim copy ----------------------------------------------------------------


def rows_from_values(values: List[List[Any]]) -> Tuple[List[str], List[Dict[str, Any]]]:
    """(header, rows) from a Sheets `values.get` result (row 1 is the header).

    A header cell is trimmed; a blank header cell is not a column. When two columns carry
    the same header the first one wins, so a stray duplicate far to the right cannot
    replace the real column's value. A row with nothing in any named column is dropped
    (it is not content, and the fingerprint ignores it anyway); a row with only a Key is
    kept, because the watcher needs to see where a key sits.
    """
    if not values:
        return [], []
    header = [str(h).strip() for h in values[0]]
    rows: List[Dict[str, Any]] = []
    for offset, raw in enumerate(values[1:]):
        cells: Dict[str, Any] = {}
        for j, name in enumerate(header):
            if not name or name in cells:
                continue
            cells[name] = raw[j] if j < len(raw) and raw[j] is not None else ""
        if any(norm(v) for v in cells.values()):
            rows.append({"row_number": offset + 2, "cells": cells})
    return header, rows


def column_letter(index: int) -> str:
    """0-based column index -> A1 letters (A … Z, AA …)."""
    s = ""
    index += 1
    while index:
        index, r = divmod(index - 1, 26)
        s = chr(65 + r) + s
    return s


def key_cell_a1(tab_name: str, header: List[str], row_number: int) -> str:
    """The A1 address of a row's Key cell. Raises KeyError when the plan has no Key column."""
    try:
        col = header.index(KEY_COLUMN)
    except ValueError:
        raise KeyError(f"kolom '{KEY_COLUMN}' tidak ada di plan") from None
    quoted = tab_name.replace("'", "''")
    return f"'{quoted}'!{column_letter(col)}{row_number}"


def key_hyperlink(jira_url: str, key: str) -> str:
    """=HYPERLINK(...) for the Key cell (USER_ENTERED), so the planner can click through."""
    return f'=HYPERLINK("{jira_url.rstrip("/")}/browse/{key}","{key}")'
