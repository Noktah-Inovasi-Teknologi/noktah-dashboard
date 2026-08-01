"""
Shared roster reconciliation engine (feature 004-relational-spine).

`reconcile_roster` turns the operator-maintained spreadsheets into the
`clients` / `client_aliases` / `accounts` / `account_handles` /
`client_account_roles` tables. It never raises for ordinary data problems
(constitution I/V) — a name conflict, a missing handle, a source disagreement
are all reported, never silently dropped or guessed at (FR-023).

Sheets are read by the caller (flows/roster_sync.py); this module takes
already-parsed rows so the reconciliation LOGIC — union of two roster sources,
alias seeding, conflict handling, idempotency — is testable without any
network I/O (see tests/test_roster_sync.py).
"""
import logging
import uuid
from typing import Any, Dict, List, Optional

import asyncpg

try:
    from ...hashmap import is_token_subset_match, normalize_client_key as _normalize_key, profile_handle
    from ...tasks.roster_tasks import (
        _upsert_account_impl,
        _upsert_alias_impl,
        _upsert_client_impl,
        _upsert_role_impl,
    )
except ImportError:
    import os
    import sys

    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    from hashmap import is_token_subset_match, normalize_client_key as _normalize_key, profile_handle
    from tasks.roster_tasks import (
        _upsert_account_impl,
        _upsert_alias_impl,
        _upsert_client_impl,
        _upsert_role_impl,
    )

logger = logging.getLogger(__name__)

# Column names read from the "Clients" worksheet.
SHEET_NAME_COLUMN = "Name"
SHEET_INSTAGRAM_COLUMN = "Instagram"
SHEET_TIKTOK_COLUMN = "TikTok"

# ---------------------------------------------------------------------------
# Explicit, documented overrides for cases the general matching rules cannot
# resolve automatically. Both tables are exactly the "reconciliation is data,
# not code" principle applied to the two known ambiguities research R1 found —
# extend them (or use roster.alias.reassign for a live correction) for any
# future case of the same shape, rather than loosening the matching rules.
# ---------------------------------------------------------------------------

# Cross-source name disagreement: the two roster sources spell one Sumenep
# client differently ("Coffee Shop" vs "Coffee Space"), and neither name's
# token set is a subset of the other's, so the general union logic below
# cannot unify them on its own. Key = normalized Clients-sheet spelling,
# value = normalized canonical spelling (the COMPONENTS-block spelling wins).
ROSTER_SOURCE_NAME_OVERRIDES: Dict[str, str] = {
    "nirwana coffee shop sumenep": "nirwana coffee space sumenep",
}

# Knowledge-base name -> canonical roster name, for the one case the
# token-subset rule matches AMBIGUOUSLY (both Nirwana outlets). Verified
# against content, not names: the records filed under "Nirwana Coffee Space"
# are byte-identical to "Nirwana Pamekasan"'s, and one explicitly names itself
# "Nirwana Coffee Space Pamekasan profile" (spec Assumptions).
KNOWLEDGE_ALIAS_OVERRIDES: Dict[str, str] = {
    "nirwana coffee space": "nirwana coffee space pamekasan",
}


def _empty_report() -> Dict[str, Any]:
    return {
        "roster_sources": {"clients_sheet": 0, "components_block": 0, "union": 0},
        "clients": {"created": 0, "updated": 0, "deactivated": 0, "unchanged": 0},
        "aliases": {"created": 0, "conflicts": []},
        "accounts": {"created": 0, "renamed": 0, "deactivated": 0, "unchanged": 0},
        "roles": {"created": 0, "changed": 0, "deactivated": 0, "unchanged": 0},
        "unresolved": [],
        "source_disagreements": [],
        "newly_inactive_accounts": [],
        "rename_candidates": [],
    }


async def reconcile_roster(
    conn: asyncpg.Connection,
    clients_sheet_rows: List[Dict[str, Any]],
    components_block: Dict[str, str],
    client_social_block: Dict[str, Dict[str, Any]],
    knowledge_base_names: Optional[List[str]] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Reconcile the datastore's roster from the two spreadsheet sources.

    Returns the standard flow-summary shape (see
    specs/004-relational-spine/contracts/roster-sync-report.md) wrapped in
    {"summary": ...} — the caller flow adds start_time/end_time/data/error.
    """
    knowledge_base_names = knowledge_base_names or []
    summary = _empty_report()

    # ---- 1. Union the two roster sources into one canonical entity per client ----
    sheet_by_norm: Dict[str, Dict[str, Any]] = {}
    for row in clients_sheet_rows:
        name = str(row.get(SHEET_NAME_COLUMN, "")).strip()
        if name:
            sheet_by_norm[_normalize_key(name)] = row
    summary["roster_sources"]["clients_sheet"] = len(sheet_by_norm)

    components_by_norm: Dict[str, str] = {}
    components_display: Dict[str, str] = {}
    for name in components_block:
        name = str(name).strip()
        if not name:
            continue
        norm = _normalize_key(name)
        components_by_norm[norm] = name
        components_display[norm] = name
    summary["roster_sources"]["components_block"] = len(components_by_norm)

    # canonical_key -> {"display_name", "sheet_row" (may be None), "in_components"}
    canonical: Dict[str, Dict[str, Any]] = {}

    for norm_name, row in sheet_by_norm.items():
        canon_key = ROSTER_SOURCE_NAME_OVERRIDES.get(norm_name, norm_name)
        comp_display = components_display.get(canon_key)
        entry = canonical.setdefault(canon_key, {
            "display_name": comp_display or str(row.get(SHEET_NAME_COLUMN, "")).strip(),
            "sheet_row": None, "in_components": canon_key in components_by_norm,
        })
        entry["sheet_row"] = row
        if canon_key != norm_name:
            summary["source_disagreements"].append({
                "kind": "name_mismatch",
                "clients_sheet": str(row.get(SHEET_NAME_COLUMN, "")).strip(),
                "components_block": comp_display or canon_key,
                "resolved_to": entry["display_name"], "via": "override",
            })

    for norm_name, display in components_display.items():
        entry = canonical.get(norm_name)
        if entry is not None:
            entry["in_components"] = True
            continue
        canonical[norm_name] = {"display_name": display, "sheet_row": None, "in_components": True}
        summary["source_disagreements"].append({
            "kind": "missing_from_clients_sheet", "name": display,
            "note": "present in COMPONENTS only; no handles, no content-type amounts",
        })

    summary["roster_sources"]["union"] = len(canonical)

    # ---- 2. Upsert clients ----
    canon_key_to_client_id: Dict[str, Optional[str]] = {}
    for canon_key, entry in canonical.items():
        result = await _upsert_client_impl(conn, canon_key, entry["display_name"], dry_run=dry_run)
        client_id = result["client_id"]
        if client_id is None and dry_run:
            # A dry-run "would-be-created" client has no real id yet. A random
            # UUID is a safe placeholder — it binds correctly against every
            # downstream UUID column, and naturally matches nothing that exists
            # yet, so alias/role lookups still report "created" accurately
            # instead of every not-yet-existing client's aliases coming back
            # as unresolved.
            client_id = uuid.uuid4()
        canon_key_to_client_id[canon_key] = client_id
        summary["clients"][result["action"] if result["action"] != "created" else "created"] += 1
        # Register both source spellings as aliases so either resolves (FR-002).
        for alias_source, alias_text in (
            ("clients_sheet", str(entry["sheet_row"].get(SHEET_NAME_COLUMN, "")).strip() if entry["sheet_row"] else None),
            ("components_block", components_display.get(canon_key)),
        ):
            if not alias_text:
                continue
            alias_result = await _upsert_alias_impl(conn, client_id, alias_text, alias_source, dry_run=dry_run)
            if alias_result["action"] == "created":
                summary["aliases"]["created"] += 1
            elif alias_result["action"] == "conflict":
                summary["aliases"]["conflicts"].append({
                    "alias": alias_result["alias_key"], "requested_client": entry["display_name"],
                    "already_owned_by_client_id": alias_result["already_owned_by_client_id"], "action": "rejected",
                })

    # ---- 3. Seed knowledge-base aliases (once, create-only — T035) ----
    active_canon_names = {canon_key: entry["display_name"] for canon_key, entry in canonical.items()}
    for kb_name in knowledge_base_names:
        norm_kb = _normalize_key(kb_name)
        client_id = None
        if norm_kb in KNOWLEDGE_ALIAS_OVERRIDES:
            target_key = KNOWLEDGE_ALIAS_OVERRIDES[norm_kb]
            client_id = canon_key_to_client_id.get(target_key)
        else:
            matches = [ck for ck, name in active_canon_names.items() if is_token_subset_match(kb_name, name)]
            if len(matches) == 1:
                client_id = canon_key_to_client_id.get(matches[0])
            elif len(matches) == 0:
                summary["unresolved"].append({"kind": "knowledge_base_name_unmatched", "name": kb_name})
                continue
            else:
                summary["unresolved"].append({
                    "kind": "knowledge_base_name_ambiguous", "name": kb_name,
                    "candidates": [active_canon_names[m] for m in matches],
                })
                continue
        if client_id is None:
            summary["unresolved"].append({"kind": "knowledge_base_name_unmatched", "name": kb_name})
            continue
        alias_result = await _upsert_alias_impl(conn, client_id, kb_name, "knowledge_base", dry_run=dry_run)
        if alias_result["action"] == "created":
            summary["aliases"]["created"] += 1
        elif alias_result["action"] == "conflict":
            summary["aliases"]["conflicts"].append({
                "alias": alias_result["alias_key"], "requested_client": kb_name,
                "already_owned_by_client_id": alias_result["already_owned_by_client_id"], "action": "rejected",
            })

    # ---- 4. Owned accounts from the Clients sheet's Instagram/TikTok columns ----
    processed_account_ids: set = set()
    for canon_key, entry in canonical.items():
        client_id = canon_key_to_client_id[canon_key]
        row = entry["sheet_row"]
        if row is None or client_id is None:
            continue
        for platform, column in (("instagram", SHEET_INSTAGRAM_COLUMN), ("tiktok", SHEET_TIKTOK_COLUMN)):
            handle = profile_handle(str(row.get(column, "")))
            if not handle:
                continue
            await _maybe_upsert_owned_account(conn, client_id, entry["display_name"], platform, handle, summary, dry_run, processed_account_ids)

    # ---- 5. Competitor accounts from CLIENT_SOCIAL ----
    for client_name, block in client_social_block.items():
        norm = _normalize_key(client_name)
        target_key = norm if norm in canon_key_to_client_id else next(
            (ck for ck in canon_key_to_client_id if is_token_subset_match(client_name, active_canon_names[ck])), None
        )
        client_id = canon_key_to_client_id.get(target_key) if target_key else None
        if client_id is None:
            summary["unresolved"].append({"kind": "client_social_name_unmatched", "name": client_name})
            continue
        for profile in block.get("competitor_profiles", []):
            handle = profile.get("handle")
            if not handle:
                continue
            account_result = await _upsert_account_impl(conn, "instagram", handle, dry_run=dry_run)
            _tally_account(summary, account_result)
            account_id = account_result["account_id"]
            if account_id is not None:
                processed_account_ids.add(account_id)
                role_result = await _upsert_role_impl(conn, client_id, account_id, "competitor", dry_run=dry_run)
                summary["roles"][role_result["action"]] += 1

    # ---- 6. Deactivate clients no longer present in either source ----
    if not dry_run:
        current_keys = list(canon_key_to_client_id.keys())
        rows = await conn.fetch(
            "SELECT id, client_key, display_name FROM clients WHERE is_active"
            + (" AND client_key <> ALL($1::text[])" if current_keys else ""),
            *([current_keys] if current_keys else []),
        )
        for r in rows:
            await conn.execute("UPDATE clients SET is_active = false, updated_at = now() WHERE id = $1", r["id"])
            summary["clients"]["deactivated"] += 1

        # Accounts/roles that exist but were not touched this run and belong to
        # a still-active client are the ones whose collection deployments keep
        # running for nothing (FR-024b) — surfaced, not silently deactivated
        # unless the account itself is no longer reachable via any handle above.
        stale_roles = await conn.fetch(
            """
            SELECT r.id, r.account_id, c.display_name AS client_name, ah.handle_text, ah.platform
            FROM client_account_roles r
            JOIN clients c ON c.id = r.client_id
            JOIN account_handles ah ON ah.account_id = r.account_id AND ah.is_current
            WHERE r.is_active AND c.is_active AND r.role = 'owned'
              AND r.account_id <> ALL($1::uuid[])
            """,
            list(processed_account_ids) if processed_account_ids else [],
        )
        for r in stale_roles:
            await conn.execute("UPDATE client_account_roles SET is_active = false, updated_at = now() WHERE id = $1", r["id"])
            await conn.execute("UPDATE accounts SET is_active = false, updated_at = now() WHERE id = $1", r["account_id"])
            summary["roles"]["deactivated"] += 1
            summary["accounts"]["deactivated"] += 1
            summary["newly_inactive_accounts"].append({
                "handle": r["handle_text"], "platform": r["platform"],
                "reason": f"no longer an owned handle for '{r['client_name']}'",
                "action_required": "remove the corresponding harvest-monthly-* deployment",
            })

    return {"summary": summary}


async def _maybe_upsert_owned_account(
    conn: asyncpg.Connection, client_id: str, client_display_name: str, platform: str, handle: str,
    summary: Dict[str, Any], dry_run: bool, processed_account_ids: set,
) -> None:
    """T065: report rather than create when this handle is unregistered but the
    client already owns an active account on this platform — that is the shape
    of a rename, and creating a second account would split the history FR-011
    protects. See contracts/account-resolution.md."""
    handle_key = handle.lower()
    existing_handle = await conn.fetchrow(
        "SELECT account_id FROM account_handles WHERE platform = $1 AND handle_key = $2", platform, handle_key
    )
    if existing_handle is None:
        current_owned = await conn.fetchrow(
            """
            SELECT r.account_id, ah.handle_text FROM client_account_roles r
            JOIN account_handles ah ON ah.account_id = r.account_id AND ah.is_current
            WHERE r.client_id = $1 AND r.role = 'owned' AND r.is_active AND ah.platform = $2
            """,
            client_id, platform,
        )
        if current_owned is not None:
            summary["rename_candidates"].append({
                "client": client_display_name, "platform": platform,
                "current_handle": current_owned["handle_text"], "new_handle": handle,
                "action_required": "confirm via roster.account.record-rename if this is the same account",
            })
            processed_account_ids.add(current_owned["account_id"])
            return

    account_result = await _upsert_account_impl(conn, platform, handle, dry_run=dry_run)
    _tally_account(summary, account_result)
    account_id = account_result["account_id"]
    if account_id is not None:
        processed_account_ids.add(account_id)
        role_result = await _upsert_role_impl(conn, client_id, account_id, "owned", dry_run=dry_run)
        summary["roles"][role_result["action"]] += 1


def _tally_account(summary: Dict[str, Any], account_result: Dict[str, Any]) -> None:
    action = account_result["action"]
    if action == "reactivated":
        summary["accounts"]["unchanged"] += 1  # reactivation is not a fresh creation
    else:
        summary["accounts"][action] += 1
