"""
The read-only sheet copies (research R5, FR-016..FR-018, G-7, G-20).

Until the automations read the Registry, the Hub keeps the Clients and Hashmaps
tabs in step with it. The rules that make that safe:
  - only the Hub's own cells are written: the nine CLIENT_COLUMNS, and the five
    Hashmaps blocks' key/value columns. "No.", "Total Minutes Equivalent", the
    extra columns past the header, and every other cell are never touched;
  - only CHANGED cells are written (diff first), in one batchUpdate per run;
  - internal Clients (Eskala) are never written to the Clients tab (FR-015), but
    stay in the Hashmaps blocks, which their own content still needs;
  - a Clients row is found by `clients.sheet_row_name` (the name last written),
    so a rename rewrites the right row instead of appending a second one;
  - Hashmaps keys that already name a Client keep their exact spelling: they are
    the lookup strings the automations use today (COMPONENTS says "Nirwana Coffee
    Space Sumenep" where the Clients tab says "…Shop…");
  - check mode diffs everything, writes the Registry's value, and reports what it
    overwrote; dry run reports the cells it would write and writes nothing.

Everything here up to `sync` is pure: rows in, cell writes out.
"""
import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import asyncpg

from . import names
from .sheet_layout import (CLIENT_COLUMNS, CLIENTS_RANGE, CLIENTS_TAB, FIRST_DATA_ROW, HASHMAPS_RANGE, HASHMAPS_TAB,
                           HEADER_NOTE, PAIR_BLOCKS, SOCIAL_COLUMNS, cell, clients_table, col_index, col_letter,
                           pair_rows, social_rows)


@dataclass
class Plan:
    cells: Dict[str, str] = field(default_factory=dict)          # "Tab!B7" → value
    overwritten: List[Dict[str, Any]] = field(default_factory=list)
    renamed_rows: Dict[str, str] = field(default_factory=dict)    # client id → name now in the sheet


def same(a: Any, b: Any, column: str = "") -> bool:
    """Equal as the sheet shows them: blanks alike, '4' == '4.0', URLs by handle."""
    a, b = ("" if a is None else str(a).strip()), ("" if b is None else str(b).strip())
    if names.is_blank(a) and names.is_blank(b):
        return True
    if column in ("Instagram", "TikTok"):
        return names.profile_handle(a) == names.profile_handle(b)
    try:
        return float(a) == float(b)
    except ValueError:
        return a == b


def _put(plan: Plan, tab: str, row: int, col: int, live: str, want: str, what: str, column: str = "") -> None:
    if same(live, want, column):
        return
    a1 = f"{tab}!{col_letter(col)}{row}"
    plan.cells[a1] = want
    plan.overwritten.append({"cell": a1, "what": what, "sheet": live, "registry": want})


# ── Clients tab ──────────────────────────────────────────────────────────────

def client_cells(c: Dict[str, Any]) -> Dict[str, str]:
    """One Registry Client as the Clients tab shows it."""
    def num(v: Optional[int]) -> str:
        return "" if v is None else str(v)

    def url(platform: str) -> str:
        handle = c.get("own", {}).get(platform)
        return names.profile_url(platform, handle) if handle else "-"
    return {"Name": c["name"], "Folder ID": c.get("drive_folder_id") or "",
            "Content Plan Folder ID": c.get("content_plan_folder_id") or "", "Status": c["status"],
            "Post": num(c.get("quota_post")), "Story": num(c.get("quota_story")),
            "Short Video": num(c.get("quota_short_video")), "Instagram": url("instagram"), "TikTok": url("tiktok")}


def plan_clients(rows: List[List[Any]], registry: List[Dict[str, Any]], plan: Plan) -> None:
    header, table = clients_table(rows)
    by_name = {names.normalize_client_key(v["Name"]): (n, v) for n, v in table}
    next_row = max([n for n, _ in table] + [len(rows)]) + 1
    for c in registry:
        if c["is_internal"]:
            continue
        want = client_cells(c)
        found = by_name.get(names.normalize_client_key(c.get("sheet_row_name") or c["name"]))
        if found is None:
            found = by_name.get(names.normalize_client_key(c["name"]))
        if found is None:
            row, live = next_row, {}
            next_row += 1
        else:
            row, live = found
        for column in CLIENT_COLUMNS:
            _put(plan, CLIENTS_TAB, row, header[column], live.get(column, ""), want[column], f"{c['name']} · {column}",
                 column)
        if (c.get("sheet_row_name") or "") != c["name"]:
            plan.renamed_rows[c["id"]] = c["name"]


# ── Hashmaps tab ─────────────────────────────────────────────────────────────

def _rewrite_block(plan: Plan, live: List[Tuple[int, List[str]]], wanted: List[Tuple[Any, List[str]]],
                   identity: Callable[[List[str]], Any], cols: Tuple[str, ...], block: str,
                   keep_first: bool) -> None:
    """Keep live order for rows still wanted (their key spelling too, when `keep_first`),
    drop rows no longer wanted, append new ones, and blank the leftover tail."""
    want = dict(wanted)
    final: List[List[str]] = []
    placed = set()
    for _, values in live:
        ident = identity(values)
        if ident in want and ident not in placed:
            row = list(want[ident])
            if keep_first:
                row[0] = values[0]
            final.append(row)
            placed.add(ident)
    final += [v for ident, v in wanted if ident not in placed]
    height = max(len(final), max((n for n, _ in live), default=FIRST_DATA_ROW - 1) - FIRST_DATA_ROW + 1)
    live_by_row = {n: v for n, v in live}
    for i in range(height):
        row_no = FIRST_DATA_ROW + i
        have = live_by_row.get(row_no, [""] * len(cols))
        target = final[i] if i < len(final) else [""] * len(cols)
        for j, letter in enumerate(cols):
            _put(plan, HASHMAPS_TAB, row_no, col_index(letter), have[j] if j < len(have) else "", target[j],
                 f"{block} baris {row_no}")


def plan_hashmaps(rows: List[List[Any]], registry: List[Dict[str, Any]], people: List[Dict[str, Any]],
                  resolve: Callable[[str], Optional[str]], plan: Plan) -> None:
    """`resolve(name)` → client id for a live Hashmaps key (aliases), or None."""
    # Internal Clients stay IN the Hashmaps blocks: FR-015 keeps them out of the Clients
    # tab only, and Eskala's own content needs its Jira component and team mappings.
    clients = registry

    def raw_block(block_cols: Tuple[str, ...]) -> List[Tuple[int, List[str]]]:
        out = []
        for n, row in enumerate(rows, start=FIRST_DATA_ROW):
            values = [cell(row, col_index(c)) for c in block_cols]
            if any(not names.is_blank(v) for v in values):
                out.append((n, values))
        return out

    # WORKERS: active People with a Jira account, keyed by the Person.
    workers_cols = PAIR_BLOCKS["WORKERS"]
    live = raw_block(workers_cols)
    person_by_name = {names.normalize_client_key(p["display_name"]): p["id"] for p in people}
    wanted = [(p["id"], [p["display_name"], p["jira_account_id"]]) for p in people
              if p["status"] == "active" and p.get("jira_account_id")]
    _rewrite_block(plan, live, wanted, lambda v: person_by_name.get(names.normalize_client_key(v[0])), workers_cols,
                   "WORKERS", keep_first=False)

    def client_block(block: str, value_of: Callable[[Dict[str, Any]], Optional[str]]) -> None:
        cols = PAIR_BLOCKS[block]
        wanted_rows = [(c["id"], [c["name"], value_of(c)]) for c in clients if value_of(c)]
        _rewrite_block(plan, raw_block(cols), wanted_rows, lambda v: resolve(v[0]), cols, block, keep_first=True)

    client_block("COMPONENTS", lambda c: c.get("jira_component_id"))
    client_block("CONTENT_EDITOR", lambda c: (c.get("team") or {}).get("content_editor"))
    client_block("FIELD_ASSOCIATE", lambda c: (c.get("team") or {}).get("field_associate"))

    # CLIENT_SOCIAL: one row per (Client, competitor). A live row's name and link
    # text are kept when the pair is still wanted.
    live_social = raw_block(SOCIAL_COLUMNS)
    names_by_pair = {}
    for _, values in live_social:
        cid = resolve(values[0])
        handle = names.profile_handle(values[2]) or names.profile_handle(values[1])
        if cid and handle:
            names_by_pair.setdefault((cid, handle), values)
    wanted_social = []
    for c in clients:
        for handle in c.get("competitors", []):
            live_values = names_by_pair.get((c["id"], handle))
            wanted_social.append(((c["id"], handle), list(live_values) if live_values else
                                  [c["name"], handle, names.profile_url("instagram", handle)]))

    def social_identity(v: List[str]) -> Any:
        cid = resolve(v[0])
        handle = names.profile_handle(v[2]) or names.profile_handle(v[1])
        return (cid, handle) if cid and handle else None
    _rewrite_block(plan, live_social, wanted_social, social_identity, SOCIAL_COLUMNS, "CLIENT_SOCIAL", keep_first=True)


# ── Registry snapshot (DB) ───────────────────────────────────────────────────

async def registry_snapshot(conn: asyncpg.Connection) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]],
                                                               Dict[str, str]]:
    rows = await conn.fetch(
        """SELECT c.id::text AS id, c.display_name AS name, COALESCE(c.status,
                  CASE WHEN c.is_active THEN 'active' ELSE 'inactive' END) AS status, c.is_internal,
                  c.quota_post, c.quota_story, c.quota_short_video, c.drive_folder_id, c.content_plan_folder_id,
                  c.jira_component_id, c.sheet_row_name
           FROM clients c ORDER BY c.display_name""")
    clients = {r["id"]: dict(r) | {"own": {}, "competitors": [], "team": {}} for r in rows}
    for r in await conn.fetch(
            """SELECT r.client_id::text AS cid, r.role, ah.platform, ah.handle_text FROM client_account_roles r
               JOIN account_handles ah ON ah.account_id = r.account_id AND ah.is_current
               WHERE r.is_active ORDER BY ah.handle_text"""):
        c = clients[r["cid"]]
        if r["role"] == "owned":
            c["own"].setdefault(r["platform"], r["handle_text"])
        elif r["role"] == "competitor" and r["platform"] == "instagram":
            c["competitors"].append(r["handle_text"])
    for r in await conn.fetch(
            """SELECT t.client_id::text AS cid, t.team_role, p.display_name FROM client_team_assignments t
               JOIN people p ON p.id = t.person_id WHERE t.valid_to IS NULL"""):
        clients[r["cid"]]["team"][r["team_role"]] = r["display_name"]
    people = [dict(r) for r in await conn.fetch(
        "SELECT id::text AS id, display_name, jira_account_id, status FROM people ORDER BY display_name")]
    aliases = {r["alias_key"]: r["cid"] for r in await conn.fetch(
        "SELECT alias_key, client_id::text AS cid FROM client_aliases")}
    for c in clients.values():
        aliases.setdefault(names.normalize_client_key(c["name"]), c["id"])
    return list(clients.values()), people, aliases


def plan_all(clients_rows: List[List[Any]], hashmap_rows: List[List[Any]], registry: List[Dict[str, Any]],
             people: List[Dict[str, Any]], aliases: Dict[str, str]) -> Plan:
    def resolve(name: str) -> Optional[str]:
        key = names.normalize_client_key(name)
        return aliases.get(key) or aliases.get(names.SOURCE_NAME_OVERRIDES.get(key, ""))
    plan = Plan()
    plan_clients(clients_rows, registry, plan)
    plan_hashmaps(hashmap_rows, registry, people, resolve, plan)
    return plan


# ── the run ──────────────────────────────────────────────────────────────────

async def sync(conn: asyncpg.Connection, spreadsheet_id: str, *, check: bool = False, dry_run: bool = False
               ) -> Dict[str, Any]:
    """One copy run. Raises on a Sheets failure (the flow retries and alerts; state
    isn't advanced, so the next run retries the same changes — never skipped, G-7)."""
    from .. import google

    state = await conn.fetchrow("SELECT * FROM hub_sync_state WHERE id = 1")
    if state is None or state["last_success_at"] is None:
        return {"status": "not_imported", "written": 0,
                "note": "Registry belum diimpor; salinan sheet belum dijalankan agar sheet tidak tertimpa."}
    last_change = await conn.fetchval("SELECT COALESCE(max(id), 0) FROM registry_changes")
    if not check and not dry_run and last_change <= state["last_change_id"]:
        return {"status": "no_changes", "written": 0}
    try:
        live = await asyncio.to_thread(google.read_ranges, spreadsheet_id, [CLIENTS_RANGE, HASHMAPS_RANGE])
        registry, people, aliases = await registry_snapshot(conn)
        plan = plan_all(live[CLIENTS_RANGE], live[HASHMAPS_RANGE], registry, people, aliases)
        result: Dict[str, Any] = {"status": "dry_run" if dry_run else "synced", "written": 0,
                                  "cells": len(plan.cells)}
        if check or dry_run:
            result["overwritten" if not dry_run else "would_write"] = plan.overwritten
        if dry_run:
            return result
        if plan.cells:
            result["written"] = await asyncio.to_thread(google.write_cells, spreadsheet_id, plan.cells)
            await asyncio.to_thread(google.set_header_notes, spreadsheet_id, [CLIENTS_TAB, HASHMAPS_TAB], HEADER_NOTE)
        for cid, name in plan.renamed_rows.items():
            await conn.execute("UPDATE clients SET sheet_row_name = $2 WHERE id = $1::uuid", cid, name)
        await conn.execute(
            f"""UPDATE hub_sync_state SET last_change_id = GREATEST(last_change_id, $1), last_success_at = now(),
                       last_error = NULL{', last_check_at = now()' if check else ''} WHERE id = 1""", last_change)
        return result
    except Exception as e:
        await conn.execute("UPDATE hub_sync_state SET last_error = $1 WHERE id = 1", f"{type(e).__name__}: {e}"[:500])
        raise
