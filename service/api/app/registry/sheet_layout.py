"""
Where the Registry lives in the legacy sheet (research R5, R6): the Clients tab's
managed columns and the Hashmaps tab's five blocks. Same letters as
service/prefect/hashmap.py SHEET_LAYOUT / CLIENT_SOCIAL_LAYOUT, which still reads
these cells until the automations switch over (docs/DEFERRED.md A-1).
"""
from typing import Any, Dict, List, Tuple

from .names import is_blank, profile_handle

CLIENTS_TAB = "Clients"
HASHMAPS_TAB = "Hashmaps"
HEADER_NOTE = "Dikelola oleh Noktah Hub — jangan edit di sini. Ubah data klien di hub.noktah.co."

# The only Clients columns the Hub writes (G-20). "No.", "Total Minutes Equivalent"
# and anything else are never touched.
CLIENT_COLUMNS = ["Name", "Folder ID", "Content Plan Folder ID", "Status", "Post", "Story", "Short Video",
                  "Instagram", "TikTok"]
FIRST_DATA_ROW = 3  # Hashmaps: row 1 block titles, row 2 column titles
PAIR_BLOCKS: Dict[str, Tuple[str, str]] = {
    "WORKERS": ("B", "C"),
    "COMPONENTS": ("F", "G"),
    "CONTENT_EDITOR": ("J", "K"),
    "FIELD_ASSOCIATE": ("N", "O"),
}
SOCIAL_COLUMNS = ("R", "S", "T")  # client, competitor name, competitor link
HASHMAPS_RANGE = f"{HASHMAPS_TAB}!A{FIRST_DATA_ROW}:T"
CLIENTS_RANGE = f"{CLIENTS_TAB}!A1:Z"


def col_index(letter: str) -> int:
    index = 0
    for char in letter.upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def col_letter(index: int) -> str:
    out = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def cell(row: List[Any], index: int) -> str:
    if index < 0 or index >= len(row):
        return ""
    value = row[index]
    return "" if value is None else str(value).strip()


def pair_rows(rows: List[List[Any]], block: str) -> List[Tuple[int, str, str]]:
    """(sheet row number, key, value) for every non-blank key, in sheet order."""
    k, v = (col_index(c) for c in PAIR_BLOCKS[block])
    out = []
    for n, row in enumerate(rows, start=FIRST_DATA_ROW):
        key = cell(row, k)
        if not is_blank(key):
            out.append((n, key, "" if is_blank(cell(row, v)) else cell(row, v)))
    return out


def social_rows(rows: List[List[Any]]) -> List[Tuple[int, str, str, str, str]]:
    """(sheet row, client, competitor name, link, handle) per competitor row, in sheet order."""
    c, nme, lnk = (col_index(x) for x in SOCIAL_COLUMNS)
    out = []
    for n, row in enumerate(rows, start=FIRST_DATA_ROW):
        client = cell(row, c)
        if is_blank(client):
            continue
        name, link = cell(row, nme), cell(row, lnk)
        handle = profile_handle(link) or profile_handle(name)
        out.append((n, client, "" if is_blank(name) else name, "" if is_blank(link) else link, handle))
    return out


def clients_table(rows: List[List[Any]]) -> Tuple[Dict[str, int], List[Tuple[int, Dict[str, str]]]]:
    """Header → column index, and (sheet row, {header: value}) for each named row."""
    if not rows:
        raise ValueError("Tab Clients kosong.")
    header = {cell(rows[0], i): i for i in range(len(rows[0])) if cell(rows[0], i)}
    missing = [c for c in CLIENT_COLUMNS if c not in header]
    if missing:
        raise ValueError(f"Tab Clients tidak punya kolom {missing}; ditemukan {list(header)}")
    out = []
    for n, row in enumerate(rows[1:], start=2):
        values = {name: cell(row, i) for name, i in header.items()}
        if not is_blank(values.get("Name", "")):
            out.append((n, values))
    return header, out
