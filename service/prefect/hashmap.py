"""
Dynamic hashmaps, sourced from the "Hashmaps" worksheet of the Clients workbook.

Previously these mappings were hardcoded here, which meant every team/client change
required a code edit + image rebuild. They now live in a Google Sheet that the ops
team owns, and this module reads them at runtime:

    https://docs.google.com/spreadsheets/d/1-aV46TIn4m_zs3vtCNeS_Bvl3Tt-tgg09uuG_NqgNNY
    tab "Hashmaps"

Sheet layout (row 1 = block titles, row 2 = column headers, row 3+ = data):

    A    B                C       E    F                G      I    J        K
    ---- WORKERS -------------    ---- COMPONENTS -----  ---- CONTENT_EDITOR ----
    No.  Name             ID      No.  Name             ID     No.  Name    Content Editor Name

    M    N                O       Q    R            S                T
    ---- FIELD_ASSOCIATE ------   ---- CLIENT_SOCIAL --------------------------------
    No.  Name  Field Associate    No.  Client Name  Competitor Name  Competitor Account Link

Rows are dynamic (blocks may have different lengths, blanks are skipped); the
*columns* are fixed and declared in `SHEET_LAYOUT` / `CLIENT_SOCIAL_LAYOUT` below —
update those constants if the sheet's horizontal layout ever changes.

Consumption is unchanged: `WORKERS`, `COMPONENTS`, `CONTENT_EDITOR`,
`FIELD_ASSOCIATE` and `CLIENT_SOCIAL` are still importable, still behave like
read-only dicts (`.get()`, `[]`, `in`, iteration), but they now resolve lazily on
first *access* — importing this module performs no network I/O.

Resilience: a successful fetch is snapshotted to `data/hashmap_cache.json` (a
bind-mounted directory, so it survives container restarts). If the Sheets API is
unreachable the snapshot is used and a warning is logged; if neither is available
a RuntimeError is raised rather than silently handing back empty mappings.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

# The Clients workbook (same spreadsheet the content-plan and songbird flows use).
HASHMAP_SPREADSHEET_ID = os.environ.get(
    "HASHMAP_SPREADSHEET_ID", "1-aV46TIn4m_zs3vtCNeS_Bvl3Tt-tgg09uuG_NqgNNY"
)
HASHMAP_TAB = os.environ.get("HASHMAP_TAB", "Hashmaps")
HASHMAP_CREDENTIALS_BLOCK = os.environ.get("HASHMAP_CREDENTIALS_BLOCK", "google-creds")

# How long a fetched copy is reused before re-reading the sheet. Short enough that
# a sheet edit lands within one flow run, long enough that a row-by-row conversion
# loop doesn't hammer the Sheets API.
HASHMAP_TTL_SECONDS = int(os.environ.get("HASHMAP_TTL_SECONDS", "300"))

# Last-known-good snapshot; `data/` is bind-mounted (docker-compose.yml).
HASHMAP_CACHE_PATH = Path(
    os.environ.get("HASHMAP_CACHE_PATH", str(Path(__file__).parent / "data" / "hashmap_cache.json"))
)

# First row holding data (rows 1-2 are the block titles and column headers).
FIRST_DATA_ROW = 3
# Rightmost column of the widest block (CLIENT_SOCIAL's "Competitor Account Link").
LAST_COLUMN = "T"

# Simple `key -> value` blocks: the sheet column holding the key and the value.
SHEET_LAYOUT: Dict[str, Dict[str, str]] = {
    # Worker name -> Jira account ID
    "WORKERS": {"key_col": "B", "value_col": "C"},
    # Client name -> Jira component ID
    "COMPONENTS": {"key_col": "F", "value_col": "G"},
    # Client name -> assigned content editor (a WORKERS key)
    "CONTENT_EDITOR": {"key_col": "J", "value_col": "K"},
    # Client name -> assigned field associate (a WORKERS key)
    "FIELD_ASSOCIATE": {"key_col": "N", "value_col": "O"},
}

# CLIENT_SOCIAL is one row *per competitor*, so it is grouped rather than paired.
CLIENT_SOCIAL_LAYOUT = {
    "client_col": "R",
    "competitor_name_col": "S",
    "competitor_link_col": "T",
}

BLOCK_NAMES: List[str] = list(SHEET_LAYOUT) + ["CLIENT_SOCIAL"]

# Cells the ops team uses to mean "nothing here".
_EMPTY_MARKERS = {"", "-", "–", "—", "n/a", "na", "none", "null", "tbd"}


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------


def _col_index(letter: str) -> int:
    """Spreadsheet column letter -> 0-based index ('A' -> 0, 'T' -> 19)."""
    index = 0
    for char in letter.strip().upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _cell(row: List[Any], index: int) -> str:
    """Read a cell as a trimmed string; out-of-range/blank cells become ''."""
    if index < 0 or index >= len(row):
        return ""
    value = row[index]
    return "" if value is None else str(value).strip()


def _is_blank(value: str) -> bool:
    return value.strip().lower() in _EMPTY_MARKERS


def profile_handle(value: str) -> str:
    """
    Normalize a profile URL (or bare handle) to the account handle that
    social-harvest stores in `harvested_signals.profile_key`.

    'https://www.instagram.com/lasikindonesia/' -> 'lasikindonesia'
    'https://www.tiktok.com/@karungjumbo'       -> 'karungjumbo'
    '@Handle'                                    -> 'handle'
    '-' / ''                                     -> ''
    """
    raw = (value or "").strip()
    if _is_blank(raw):
        return ""
    if "/" in raw or raw.lower().startswith("http"):
        path = urlparse(raw if "://" in raw else f"https://{raw}").path
        segments = [seg for seg in path.split("/") if seg]
        raw = segments[-1] if segments else ""
    return raw.lstrip("@").strip().lower()


def _parse_pair_block(rows: List[List[Any]], key_col: str, value_col: str, block: str) -> Dict[str, str]:
    """Parse a two-column `key -> value` block, skipping blank/partial rows."""
    key_index, value_index = _col_index(key_col), _col_index(value_col)
    parsed: Dict[str, str] = {}
    for row_number, row in enumerate(rows, start=FIRST_DATA_ROW):
        key = _cell(row, key_index)
        if _is_blank(key):
            continue
        value = _cell(row, value_index)
        if _is_blank(value):
            logger.warning(f"{block}: row {row_number} '{key}' has no value in column {value_col}; skipped")
            continue
        if key in parsed and parsed[key] != value:
            logger.warning(f"{block}: duplicate key '{key}' at row {row_number}; using the last value")
        parsed[key] = value
    return parsed


def _parse_client_social(rows: List[List[Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Parse the CLIENT_SOCIAL block: one row per (client, competitor) pair, grouped
    into `{client: {"own": [...], "competitors": [handle, ...], "competitor_profiles": [...]}}`.

    Only competitors are configured in the sheet, so `own` is always [] here —
    songbird merges per-run handle overrides on top (see `_resolve_handles`).
    Clients with no competitor rows are simply absent, and songbird degrades to
    knowledge-base + marketing params (FR-011).
    """
    client_index = _col_index(CLIENT_SOCIAL_LAYOUT["client_col"])
    name_index = _col_index(CLIENT_SOCIAL_LAYOUT["competitor_name_col"])
    link_index = _col_index(CLIENT_SOCIAL_LAYOUT["competitor_link_col"])

    parsed: Dict[str, Dict[str, Any]] = {}
    for row_number, row in enumerate(rows, start=FIRST_DATA_ROW):
        client = _cell(row, client_index)
        if _is_blank(client):
            continue
        link = _cell(row, link_index)
        name = _cell(row, name_index)
        handle = profile_handle(link) or profile_handle(name)
        if not handle:
            logger.warning(
                f"CLIENT_SOCIAL: row {row_number} for '{client}' has no usable competitor handle; skipped"
            )
            continue
        entry = parsed.setdefault(client, {"own": [], "competitors": [], "competitor_profiles": []})
        if handle in entry["competitors"]:
            continue
        entry["competitors"].append(handle)
        entry["competitor_profiles"].append(
            {"name": "" if _is_blank(name) else name, "url": "" if _is_blank(link) else link, "handle": handle}
        )
    return parsed


def parse_hashmap_rows(rows: List[List[Any]]) -> Dict[str, Any]:
    """Turn raw `Hashmaps!A3:T` values into the five mapping blocks."""
    parsed: Dict[str, Any] = {
        block: _parse_pair_block(rows, layout["key_col"], layout["value_col"], block)
        for block, layout in SHEET_LAYOUT.items()
    }
    parsed["CLIENT_SOCIAL"] = _parse_client_social(rows)
    return parsed


# --------------------------------------------------------------------------
# Fetching (sheet -> disk snapshot -> in-memory cache)
# --------------------------------------------------------------------------


async def _read_rows_async() -> List[List[Any]]:
    """Read the Hashmaps data range via the shared Google credentials block."""
    from blocks.google_credentials import GoogleCredentials  # local: keeps import side-effect free

    credentials = await GoogleCredentials.load_or_env(HASHMAP_CREDENTIALS_BLOCK)
    sheets = credentials.get_client().sheets_service
    response = (
        sheets.spreadsheets()
        .values()
        .get(
            spreadsheetId=HASHMAP_SPREADSHEET_ID,
            range=f"{HASHMAP_TAB}!A{FIRST_DATA_ROW}:{LAST_COLUMN}",
        )
        .execute()
    )
    return response.get("values", [])


def _read_rows() -> List[List[Any]]:
    """
    Synchronous wrapper around `_read_rows_async`.

    Callers are a mix of sync Prefect tasks and sync helpers invoked from inside
    async flows, so the coroutine is always driven on a dedicated thread with its
    own event loop — `asyncio.run` would raise if a loop is already running on the
    calling thread.
    """
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="hashmap-fetch") as pool:
        return pool.submit(lambda: asyncio.run(_read_rows_async())).result()


def _write_snapshot(data: Dict[str, Any]) -> None:
    """Persist the last known good copy (best-effort — never breaks a fetch)."""
    try:
        HASHMAP_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp_path = HASHMAP_CACHE_PATH.with_suffix(".tmp")
        payload = {"fetched_at": time.time(), "spreadsheet_id": HASHMAP_SPREADSHEET_ID, "blocks": data}
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(HASHMAP_CACHE_PATH)
    except Exception as exc:
        logger.warning(f"Could not write hashmap snapshot to {HASHMAP_CACHE_PATH}: {exc}")


def _read_snapshot() -> Optional[Dict[str, Any]]:
    """Load the last known good copy, or None if there isn't a usable one."""
    try:
        if not HASHMAP_CACHE_PATH.exists():
            return None
        payload = json.loads(HASHMAP_CACHE_PATH.read_text(encoding="utf-8"))
        blocks = payload.get("blocks") or {}
        if not all(name in blocks for name in BLOCK_NAMES):
            return None
        return blocks
    except Exception as exc:
        logger.warning(f"Could not read hashmap snapshot from {HASHMAP_CACHE_PATH}: {exc}")
        return None


_cache: Dict[str, Any] = {"blocks": None, "fetched_at": 0.0}
_cache_lock = threading.Lock()


def load_hashmaps(force: bool = False) -> Dict[str, Any]:
    """
    Return every mapping block, reading the sheet when the cached copy is stale.

    Args:
        force: Ignore the TTL and re-read the sheet now.

    Returns:
        {"WORKERS": {...}, "COMPONENTS": {...}, "CONTENT_EDITOR": {...},
         "FIELD_ASSOCIATE": {...}, "CLIENT_SOCIAL": {...}}

    Raises:
        RuntimeError: the sheet could not be read and no snapshot exists — better
            to fail loudly than to hand back empty mappings that would silently
            produce Jira issues with no component or assignees.
    """
    with _cache_lock:
        fresh = (
            _cache["blocks"] is not None
            and (time.time() - _cache["fetched_at"]) < HASHMAP_TTL_SECONDS
        )
        if fresh and not force:
            return _cache["blocks"]

        try:
            blocks = parse_hashmap_rows(_read_rows())
            logger.info(
                "Loaded hashmaps from '%s': %s",
                HASHMAP_TAB,
                ", ".join(f"{name}={len(blocks[name])}" for name in BLOCK_NAMES),
            )
            _write_snapshot(blocks)
        except Exception as exc:
            snapshot = _read_snapshot()
            if snapshot is None:
                if _cache["blocks"] is not None:
                    logger.warning(f"Hashmap refresh failed ({exc}); reusing the previous in-memory copy")
                    _cache["fetched_at"] = time.time()
                    return _cache["blocks"]
                raise RuntimeError(
                    f"Could not read the '{HASHMAP_TAB}' sheet ({exc}) and no local snapshot "
                    f"exists at {HASHMAP_CACHE_PATH}"
                ) from exc
            logger.warning(f"Could not read the '{HASHMAP_TAB}' sheet ({exc}); using the last-known-good snapshot")
            blocks = snapshot

        _cache["blocks"] = blocks
        _cache["fetched_at"] = time.time()
        return blocks


def refresh_hashmaps() -> Dict[str, int]:
    """Force a re-read of the sheet; returns the row count per block."""
    blocks = load_hashmaps(force=True)
    return {name: len(blocks[name]) for name in BLOCK_NAMES}


# --------------------------------------------------------------------------
# Public mappings
# --------------------------------------------------------------------------


class _SheetMapping(Mapping):
    """
    Read-only dict view over one sheet block, resolved on first access.

    Deferring the fetch to access time (rather than import time) keeps `import
    hashmap` cheap and offline-safe, while `WORKERS.get(name)` and friends keep
    working exactly as they did when these were plain dicts.
    """

    __slots__ = ("_block",)

    def __init__(self, block: str) -> None:
        self._block = block

    def _resolve(self) -> Dict[str, Any]:
        return load_hashmaps()[self._block]

    def __getitem__(self, key: str) -> Any:
        return self._resolve()[key]

    def __iter__(self):
        return iter(self._resolve())

    def __len__(self) -> int:
        return len(self._resolve())

    def __repr__(self) -> str:
        # Deliberately does not resolve — repr() must stay side-effect free.
        return f"<hashmap {self._block} (Google Sheet '{HASHMAP_TAB}')>"


# Worker name -> Jira account ID
WORKERS = _SheetMapping("WORKERS")
# Client name -> Jira component ID
COMPONENTS = _SheetMapping("COMPONENTS")
# Client name -> assigned content editor name (a WORKERS key)
CONTENT_EDITOR = _SheetMapping("CONTENT_EDITOR")
# Client name -> assigned field associate name (a WORKERS key)
FIELD_ASSOCIATE = _SheetMapping("FIELD_ASSOCIATE")
# Client name -> {"own": [...], "competitors": [handle, ...], "competitor_profiles": [...]}
# feeding songbird's "what hits" signal (feature 003). Handles are matched against
# harvested_signals.profile_key. Clients with no competitor rows are absent, and
# songbird falls back to client-knowledge + marketing params only.
CLIENT_SOCIAL = _SheetMapping("CLIENT_SOCIAL")


if __name__ == "__main__":  # pragma: no cover - manual inspection helper
    import argparse

    parser = argparse.ArgumentParser(description="Inspect the sheet-backed hashmaps")
    parser.add_argument("--block", choices=BLOCK_NAMES, help="Print a single block instead of a summary")
    parser.add_argument("--json", action="store_true", help="Print full contents as JSON")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    loaded = load_hashmaps(force=True)

    if args.block:
        print(json.dumps(loaded[args.block], ensure_ascii=False, indent=2))
    elif args.json:
        print(json.dumps(loaded, ensure_ascii=False, indent=2))
    else:
        for block_name in BLOCK_NAMES:
            print(f"{block_name}: {len(loaded[block_name])} entries")
