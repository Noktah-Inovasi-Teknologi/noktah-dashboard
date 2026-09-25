"""
The one-time Registry import (research R6, FR-014, G-19).

Reads the Clients tab and the Hashmaps blocks, and brings the database's Registry
in line with them:
  - Clients are matched through `client_aliases` (seeded by roster-sync), then the
    documented override, then a UNIQUE token-subset match. Anything else is a new
    Client or an unmatched name — never a guess.
  - The sheet wins for name, status, quotas and folders; every difference is listed.
  - Existing accounts and history are linked, never recreated or deleted. Links the
    database has and the sheet doesn't are reported, not removed.
  - Every Client gets the Eskala Noktah Brand; Eskala itself is an internal Client (G-18).
  - People come from WORKERS (name + Jira ID). A WORKERS name that matches a seeded
    Person links to it, and the WORKERS spelling becomes the display name, since that
    exact string is the key hashmap.py resolves.
  - CONTENT_EDITOR / FIELD_ASSOCIATE become team assignments; COMPONENTS becomes
    each Client's Jira component.

Validate-only runs the SAME code inside a transaction that is rolled back, so the
report is exactly what a real run would do. Data problems are reported, never raised.
"""
from typing import Any, Dict, List, Optional

import asyncpg

from ..errors import HubError
from ..people import service as people_service
from . import names, service
from .changes import record_change
from .sheet_layout import clients_table, pair_rows, social_rows

STATUS_WORDS = {"active": "active", "aktif": "active", "inactive": "inactive", "tidak aktif": "inactive",
                "nonaktif": "inactive", "pending": "pending", "menunggu": "pending"}
QUOTA_COLUMNS = {"Post": "quota_post", "Story": "quota_story", "Short Video": "quota_short_video"}
TEXT_COLUMNS = {"Folder ID": "drive_folder_id", "Content Plan Folder ID": "content_plan_folder_id"}
INTERNAL_CLIENT = "Eskala"


class _Rollback(Exception):
    pass


def _report(validate_only: bool) -> Dict[str, Any]:
    return {"validate_only": validate_only,
            "clients": {"in_sheet": 0, "matched": [], "created": [], "only_in_database": []},
            "differences": [], "unmatched": [], "skipped": [],
            "people": {"created": [], "linked": []},
            "team": [], "components": [],
            "accounts": {"linked": [], "only_in_database": []},
            "roster_sync": None}


class Resolver:
    """Client name → id, using what the database already knows."""

    def __init__(self, rows: List[asyncpg.Record], aliases: List[asyncpg.Record]):
        self.by_alias = {a["alias_key"]: a["client_id"] for a in aliases}
        self.names = {r["id"]: r["display_name"] for r in rows}
        for r in rows:
            self.by_alias.setdefault(r["client_key"], r["id"])

    def add(self, client_id: str, name: str) -> None:
        self.names[client_id] = name
        self.by_alias.setdefault(names.normalize_client_key(name), client_id)

    def resolve(self, name: str) -> tuple[Optional[str], List[str]]:
        key = names.normalize_client_key(name)
        for k in (key, names.SOURCE_NAME_OVERRIDES.get(key)):
            if k and k in self.by_alias:
                return self.by_alias[k], []
        hits = sorted({cid for cid, n in self.names.items() if names.is_token_subset_match(name, n)})
        if len(hits) == 1:
            return hits[0], []
        return None, [self.names[h] for h in hits]


def _int_or_skip(value: str) -> tuple[bool, Optional[int]]:
    if names.is_blank(value):
        return True, None
    try:
        number = float(value.replace(",", "."))
    except ValueError:
        return False, None
    return (number >= 0 and number == int(number)), int(number)


async def run_import(conn: asyncpg.Connection, clients_rows: List[List[Any]], hashmap_rows: List[List[Any]],
                     validate_only: bool) -> Dict[str, Any]:
    report = _report(validate_only)
    try:
        async with conn.transaction():
            await _import(conn, clients_rows, hashmap_rows, report)
            if validate_only:
                raise _Rollback()
    except _Rollback:
        pass
    return report


async def _import(conn: asyncpg.Connection, clients_rows: List[List[Any]], hashmap_rows: List[List[Any]],
                  report: Dict[str, Any]) -> None:
    eskala = await conn.fetchval("SELECT id FROM noktah_brands WHERE brand_key = 'eskala'")
    resolver = Resolver(await conn.fetch("SELECT id::text AS id, client_key, display_name FROM clients"),
                        await conn.fetch("SELECT alias_key, client_id::text AS client_id FROM client_aliases"))
    _, sheet = clients_table(clients_rows)
    report["clients"]["in_sheet"] = len(sheet)
    seen: Dict[str, str] = {}

    # ── Clients tab ──────────────────────────────────────────────────────────
    for row_no, values in sheet:
        name = " ".join(values["Name"].split())
        cid, candidates = resolver.resolve(name)
        if cid is None and candidates:
            report["unmatched"].append({"source": "Clients", "row": row_no, "name": name,
                                        "reason": f"cocok dengan lebih dari satu klien: {candidates}"})
            continue
        if cid in seen:
            report["unmatched"].append({"source": "Clients", "row": row_no, "name": name,
                                        "reason": f"baris ganda untuk {seen[cid]}"})
            continue
        status_raw = values.get("Status", "").strip().lower()
        status = STATUS_WORDS.get(status_raw)
        if status is None:
            report["skipped"].append({"source": "Clients", "row": row_no, "name": name, "field": "status",
                                      "value": values.get("Status", ""), "reason": "status tidak dikenal; dianggap active"})
            status = "active"
        if cid is None:
            cid = await service.create_client(conn, name=name, brand="eskala", status=status, person_id=None)
            resolver.add(cid, name)
            report["clients"]["created"].append(name)
        else:
            report["clients"]["matched"].append({"sheet": name, "database": resolver.names[cid]})
        seen[cid] = name
        current = await conn.fetchrow("SELECT * FROM clients WHERE id = $1::uuid", cid)

        changes: Dict[str, Any] = {"display_name": name, "status": status}
        for col, field in QUOTA_COLUMNS.items():
            ok, number = _int_or_skip(values.get(col, ""))
            if ok:
                changes[field] = number
            else:
                report["skipped"].append({"source": "Clients", "row": row_no, "name": name, "field": field,
                                          "value": values.get(col), "reason": "bukan angka bulat ≥ 0"})
        for col, field in TEXT_COLUMNS.items():
            changes[field] = None if names.is_blank(values.get(col, "")) else values[col].strip()
        for field, new in changes.items():
            if current[field] != new:
                report["differences"].append({"client": name, "field": field, "database": current[field],
                                              "sheet": new, "action": "sheet_wins"})
        try:  # the name alone: an alias conflict must not block status, quotas and folders
            await service._apply(conn, cid, {"display_name": changes.pop("display_name")}, None, current)
        except HubError as e:
            report["skipped"].append({"source": "Clients", "row": row_no, "name": name, "field": "name",
                                      "value": name, "reason": e.message})
        await service._apply(conn, cid, changes, None, await conn.fetchrow("SELECT * FROM clients WHERE id = $1::uuid", cid))
        await conn.execute(
            """UPDATE clients SET sheet_row_name = $2, noktah_brand_id = COALESCE(noktah_brand_id, $3),
                                  version = version + 1, updated_at = now() WHERE id = $1::uuid""",
            cid, values["Name"], eskala)
        if current["noktah_brand_id"] is None:
            await record_change(conn, entity="client", entity_id=cid, client_id=cid, field="noktah_brand",
                                old=None, new="eskala", person_id=None)

        for platform, col in (("instagram", "Instagram"), ("tiktok", "TikTok")):
            handle = names.profile_handle(values.get(col, ""))
            if handle:
                await _link(conn, cid, name, platform, handle, "owned", report)

    for cid, name in sorted(resolver.names.items(), key=lambda x: x[1]):
        if cid not in seen:
            report["clients"]["only_in_database"].append(name)

    # ── Eskala, internal (G-18) ─────────────────────────────────────────────
    cid, _ = resolver.resolve(INTERNAL_CLIENT)
    if cid is None:
        cid = await service.create_client(conn, name=INTERNAL_CLIENT, brand="eskala", status="active",
                                          is_internal=True, person_id=None)
        resolver.add(cid, INTERNAL_CLIENT)
        report["clients"]["created"].append(f"{INTERNAL_CLIENT} (internal)")
    else:
        await conn.execute("UPDATE clients SET is_internal = true, noktah_brand_id = COALESCE(noktah_brand_id, $2) "
                           "WHERE id = $1::uuid", cid, eskala)
        if INTERNAL_CLIENT in report["clients"]["only_in_database"]:
            report["clients"]["only_in_database"].remove(INTERNAL_CLIENT)

    # ── People from WORKERS ─────────────────────────────────────────────────
    people = {r["id"]: r for r in await conn.fetch("SELECT id::text AS id, display_name, jira_account_id FROM people")}
    by_name: Dict[str, str] = {}
    for _, worker, jira in pair_rows(hashmap_rows, "WORKERS"):
        worker = " ".join(worker.split())
        key = names.normalize_client_key(worker)
        pid = next((p for p, r in people.items() if names.normalize_client_key(r["display_name"]) == key), None)
        if pid is None:
            hits = [p for p, r in people.items() if names.is_token_subset_match(worker, r["display_name"])
                    and p not in by_name.values()]
            pid = hits[0] if len(hits) == 1 else None
        if pid is None:
            pid = await conn.fetchval(
                "INSERT INTO people (display_name, jira_account_id) VALUES ($1, $2) RETURNING id::text",
                worker, jira or None)
            await record_change(conn, entity="person", entity_id=pid, client_id=None, field="created", old=None,
                                new={"name": worker, "jira_account_id": jira or None}, person_id=None)
            people[pid] = {"id": pid, "display_name": worker, "jira_account_id": jira or None}
            report["people"]["created"].append(worker)
        else:
            old = people[pid]
            if old["display_name"] != worker or (jira and old["jira_account_id"] != jira):
                await conn.execute("UPDATE people SET display_name = $2, jira_account_id = COALESCE($3, jira_account_id),"
                                   " updated_at = now() WHERE id = $1::uuid", pid, worker, jira or None)
                await record_change(conn, entity="person", entity_id=pid, client_id=None, field="linked_to_workers",
                                    old={"name": old["display_name"], "jira_account_id": old["jira_account_id"]},
                                    new={"name": worker, "jira_account_id": jira or old["jira_account_id"]},
                                    person_id=None)
            report["people"]["linked"].append({"workers": worker, "person": old["display_name"]})
        by_name[key] = pid

    # ── Team (CONTENT_EDITOR, FIELD_ASSOCIATE) ───────────────────────────────
    for block, role in (("CONTENT_EDITOR", "content_editor"), ("FIELD_ASSOCIATE", "field_associate")):
        for row_no, client_name, person_name in pair_rows(hashmap_rows, block):
            cid, cands = resolver.resolve(client_name)
            pid = by_name.get(names.normalize_client_key(person_name))
            if cid is None or pid is None:
                report["unmatched"].append({
                    "source": block, "row": row_no, "name": client_name if cid is None else person_name,
                    "reason": ("klien tidak ditemukan" + (f" (kandidat: {cands})" if cands else "")) if cid is None
                    else "orang tidak ada di WORKERS"})
                continue
            current = await conn.fetchrow(
                """SELECT p.display_name FROM client_team_assignments t JOIN people p ON p.id = t.person_id
                   WHERE t.client_id = $1::uuid AND t.team_role = $2 AND t.valid_to IS NULL""", cid, role)
            if current and current["display_name"] == people[pid]["display_name"]:
                continue
            version = await conn.fetchval("SELECT version FROM clients WHERE id = $1::uuid", cid)
            await people_service.ensure_role(conn, pid, role, "eskala", None)  # a team slot needs its role
            await service.set_team(conn, cid, version, role, pid, None)
            report["team"].append({"client": resolver.names[cid], "role": role,
                                   "database": current["display_name"] if current else None,
                                   "sheet": people[pid]["display_name"]})

    # ── COMPONENTS → Jira component ──────────────────────────────────────────
    for row_no, client_name, component in pair_rows(hashmap_rows, "COMPONENTS"):
        cid, cands = resolver.resolve(client_name)
        if cid is None:
            report["unmatched"].append({"source": "COMPONENTS", "row": row_no, "name": client_name,
                                        "reason": "klien tidak ditemukan" + (f" (kandidat: {cands})" if cands else "")})
            continue
        current = await conn.fetchrow("SELECT * FROM clients WHERE id = $1::uuid", cid)
        if current["jira_component_id"] != (component or None):
            report["components"].append({"client": resolver.names[cid], "database": current["jira_component_id"],
                                         "sheet": component or None})
            await service._apply(conn, cid, {"jira_component_id": component or None}, None, current)
        # The COMPONENTS spelling stays a lookup name for this Client (roster-sync did the same).
        try:
            await service.add_alias(conn, cid, client_name, "components_block")
        except HubError as e:
            report["skipped"].append({"source": "COMPONENTS", "row": row_no, "name": client_name, "field": "alias",
                                      "value": client_name, "reason": e.message})

    # ── CLIENT_SOCIAL → competitor links ─────────────────────────────────────
    for row_no, client_name, _comp_name, _url, handle in social_rows(hashmap_rows):
        cid, cands = resolver.resolve(client_name)
        if cid is None or not handle:
            report["unmatched"].append({"source": "CLIENT_SOCIAL", "row": row_no, "name": client_name,
                                        "reason": "tidak ada handle pesaing" if cid else
                                        "klien tidak ditemukan" + (f" (kandidat: {cands})" if cands else "")})
            continue
        await _link(conn, cid, resolver.names[cid], "instagram", handle, "competitor", report)

    # Links the database has that the sheet doesn't mention: kept, and listed.
    listed = {(a["client"], a["account"]) for a in report["accounts"]["linked"]}
    for r in await conn.fetch(
            """SELECT c.display_name, ah.platform, ah.handle_text, r.role FROM client_account_roles r
               JOIN clients c ON c.id = r.client_id
               JOIN account_handles ah ON ah.account_id = r.account_id AND ah.is_current
               WHERE r.is_active ORDER BY 1, 2, 3"""):
        if (r["display_name"], f"{r['platform']}:{r['handle_text']}") not in listed:
            report["accounts"]["only_in_database"].append(
                {"client": r["display_name"], "account": f"{r['platform']}:{r['handle_text']}", "relation": r["role"]})

    # The sheet already matches the Registry: the copy starts from here (R5).
    await conn.execute("UPDATE hub_sync_state SET last_change_id = (SELECT COALESCE(max(id), 0) FROM registry_changes),"
                       " last_success_at = now(), last_error = NULL WHERE id = 1")


async def _link(conn: asyncpg.Connection, client_id: str, client_name: str, platform: str, handle: str, role: str,
                report: Dict[str, Any]) -> None:
    account_id = await service.resolve_account(conn, platform, handle)
    label = f"{platform}:{handle}"
    existing = await conn.fetchrow(
        "SELECT id, role, is_active FROM client_account_roles WHERE client_id = $1::uuid AND account_id = $2::uuid",
        client_id, account_id)
    action = "unchanged"
    if existing is None or existing["role"] != role or not existing["is_active"]:
        if role == "owned":
            other = await service._owned_elsewhere(conn, account_id, client_id)
            if other:
                report["skipped"].append({"source": "Clients", "name": client_name, "field": platform, "value": handle,
                                          "reason": f"akun sudah milik {other}"})
                return
        if existing is None:
            await conn.execute("INSERT INTO client_account_roles (client_id, account_id, role) "
                               "VALUES ($1::uuid, $2::uuid, $3)", client_id, account_id, role)
            action = "linked"
        else:
            await conn.execute("UPDATE client_account_roles SET role = $2, is_active = true, updated_at = now() "
                               "WHERE id = $1", existing["id"], role)
            action = "updated"
        await record_change(conn, entity="account_link", entity_id=account_id, client_id=client_id, field="relation",
                            old=None if existing is None else {"account": label, "relation": existing["role"],
                                                                "is_active": existing["is_active"]},
                            new={"account": label, "relation": role, "is_active": True}, person_id=None)
    report["accounts"]["linked"].append({"client": client_name, "account": label, "relation": role, "action": action})
