"""
Registry writes (US4). Every change is logged in `registry_changes` inside the
same transaction (FR-013), bumps `clients.version` (optimistic concurrency,
FR-044), and keeps the legacy `is_active` flag in step with `status` so the
automations still reading `is_active` see the same roster.

Nothing is deleted (G-32): a Client leaves by `status = inactive`, a team member
by an ended assignment, an account link by `is_active = false`.
"""
from typing import Any, Dict, Optional

import asyncpg

from ..errors import Conflict, Invalid, NotFound
from ..people import catalog
from . import names
from .changes import record_change

STATUSES = ("pending", "active", "inactive")
EDITABLE = ("display_name", "status", "quota_post", "quota_story", "quota_short_video", "drive_folder_id",
            "content_plan_folder_id", "jira_component_id")
RELATIONS = {"own": "owned", "competitor": "competitor"}
PLATFORMS = ("instagram", "tiktok")


async def lock_client(conn: asyncpg.Connection, client_id: str, version: Optional[int]) -> asyncpg.Record:
    row = await conn.fetchrow("SELECT * FROM clients WHERE id = $1::uuid FOR UPDATE", client_id)
    if row is None:
        raise NotFound("Klien tidak ditemukan.")
    if version is None:
        raise Invalid("Versi data wajib dikirim.")
    if version != row["version"]:
        raise Conflict("Data klien sudah diubah orang lain. Muat ulang lalu coba lagi.", version=row["version"])
    return row


async def bump(conn: asyncpg.Connection, client_id: str) -> int:
    return await conn.fetchval(
        "UPDATE clients SET version = version + 1, updated_at = now() WHERE id = $1::uuid RETURNING version", client_id)


async def add_alias(conn: asyncpg.Connection, client_id: str, text: str, source: str) -> None:
    key = names.normalize_client_key(text)
    owner = await conn.fetchval("SELECT client_id::text FROM client_aliases WHERE alias_key = $1", key)
    if owner is None:
        await conn.execute(
            "INSERT INTO client_aliases (client_id, alias_key, alias_text, source) VALUES ($1::uuid, $2, $3, $4)",
            client_id, key, text.strip(), source)
    elif owner != client_id:
        other = await conn.fetchval("SELECT display_name FROM clients WHERE id = $1::uuid", owner)
        raise Invalid(f"Nama “{text.strip()}” sudah dipakai klien lain: {other}.")


def _clean(field: str, value: Any) -> Any:
    if field == "display_name":
        if not isinstance(value, str) or not value.strip():
            raise Invalid("Nama klien wajib diisi.")
        return " ".join(value.split())
    if field == "status":
        if value not in STATUSES:
            raise Invalid("Status harus pending, active, atau inactive.")
        return value
    if field.startswith("quota_"):
        if value is None or value == "":
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise Invalid("Kuota harus angka 0 atau lebih.")
        return value
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return str(value).strip()


async def create_client(conn: asyncpg.Connection, *, name: str, brand: str, person_id: Optional[str],
                        status: str = "pending", is_internal: bool = False, fields: Optional[Dict[str, Any]] = None
                        ) -> str:
    name = _clean("display_name", name)
    brand_id = await conn.fetchval("SELECT id FROM noktah_brands WHERE brand_key = $1 AND kind = 'brand'", brand)
    if brand_id is None:
        raise Invalid("Noktah Brand tidak dikenal.")
    key = names.normalize_client_key(name)
    if await conn.fetchval("SELECT 1 FROM client_aliases WHERE alias_key = $1", key) or \
            await conn.fetchval("SELECT 1 FROM clients WHERE client_key = $1", key):
        raise Invalid(f"Klien bernama “{name}” sudah ada.")
    status = _clean("status", status)
    cid = await conn.fetchval(
        """INSERT INTO clients (client_key, display_name, noktah_brand_id, status, is_active, is_internal)
           VALUES ($1, $2, $3, $4, $5, $6) RETURNING id::text""",
        key, name, brand_id, status, status != "inactive", is_internal)
    await add_alias(conn, cid, name, "manual")
    await record_change(conn, entity="client", entity_id=cid, client_id=cid, field="created", old=None,
                        new={"name": name, "brand": brand, "status": status, "is_internal": is_internal},
                        person_id=person_id)
    if fields:
        await _apply(conn, cid, {k: v for k, v in fields.items() if k in EDITABLE and k != "display_name"}, person_id,
                     current=await conn.fetchrow("SELECT * FROM clients WHERE id = $1::uuid", cid))
    return cid


async def _apply(conn: asyncpg.Connection, client_id: str, changes: Dict[str, Any], person_id: Optional[str],
                 current: asyncpg.Record) -> int:
    changed = 0
    for field, raw in changes.items():
        if field not in EDITABLE:
            raise Invalid(f"Kolom {field} tidak bisa diubah di sini.")
        value = _clean(field, raw)
        if value == current[field]:
            continue
        if field == "display_name":
            await add_alias(conn, client_id, value, "manual")
        await conn.execute(f"UPDATE clients SET {field} = $2 WHERE id = $1::uuid", client_id, value)
        if field == "status":
            await conn.execute("UPDATE clients SET is_active = $2 WHERE id = $1::uuid", client_id, value != "inactive")
        await record_change(conn, entity="client", entity_id=client_id, client_id=client_id, field=field,
                            old=current[field], new=value, person_id=person_id)
        changed += 1
    return changed


async def update_client(conn: asyncpg.Connection, client_id: str, version: Optional[int], changes: Dict[str, Any],
                        person_id: Optional[str]) -> int:
    current = await lock_client(conn, client_id, version)
    if await _apply(conn, client_id, changes, person_id, current):
        return await bump(conn, client_id)
    return current["version"]


async def set_team(conn: asyncpg.Connection, client_id: str, version: Optional[int], team_role: str,
                   person_id_to_set: Optional[str], person_id: Optional[str]) -> int:
    """Fill or clear one team slot. Only a Person holding that role in the Client's
    brand may fill it (roles live on the Person, migration 012)."""
    await lock_client(conn, client_id, version)
    brand = await conn.fetchval(
        """SELECT b.brand_key FROM clients c JOIN noktah_brands b ON b.id = c.noktah_brand_id
           WHERE c.id = $1::uuid""", client_id)
    current = await conn.fetchrow(
        """SELECT t.id, t.person_id::text AS person_id, p.display_name FROM client_team_assignments t
           JOIN people p ON p.id = t.person_id
           WHERE t.client_id = $1::uuid AND t.team_role = $2 AND t.valid_to IS NULL""", client_id, team_role)
    if (current["person_id"] if current else None) == person_id_to_set:
        return await conn.fetchval("SELECT version FROM clients WHERE id = $1::uuid", client_id)
    if team_role not in await catalog.team_slots(conn, brand) and not (current and person_id_to_set is None):
        raise NotFound("Peran tim tidak dikenal.")
    new_name = None
    if person_id_to_set:
        person = await conn.fetchrow("SELECT display_name, status FROM people WHERE id = $1::uuid", person_id_to_set)
        if person is None or person["status"] != "active":
            raise Invalid("Pilih orang yang masih aktif.")  # FR-011: never free text, never a leaver
        if not await conn.fetchval(
                """SELECT 1 FROM person_roles r JOIN noktah_brands b ON b.id = r.noktah_brand_id
                   WHERE r.person_id = $1::uuid AND r.role = $2 AND b.brand_key = $3 AND r.valid_to IS NULL""",
                person_id_to_set, team_role, brand):
            raise Invalid(f"{person['display_name']} tidak memegang peran ini di {brand.capitalize()}. "
                          "Beri perannya dulu di halaman Orang.")
        new_name = person["display_name"]
    if current:
        await conn.execute("UPDATE client_team_assignments SET valid_to = now() WHERE id = $1", current["id"])
    if person_id_to_set:
        await conn.execute(
            """INSERT INTO client_team_assignments (client_id, team_role, person_id, set_by)
               VALUES ($1::uuid, $2, $3::uuid, $4::uuid)""", client_id, team_role, person_id_to_set, person_id)
    await record_change(conn, entity="team", entity_id=client_id, client_id=client_id, field=team_role,
                        old=current["display_name"] if current else None, new=new_name, person_id=person_id)
    return await bump(conn, client_id)


async def resolve_account(conn: asyncpg.Connection, platform: str, handle_or_url: str) -> str:
    """The account for a handle on a platform, created when new (same keys as roster-sync)."""
    if platform not in PLATFORMS:
        raise Invalid("Platform harus instagram atau tiktok.")
    handle = names.profile_handle(handle_or_url)
    if not handle:
        raise Invalid("Handle akun wajib diisi.")
    row = await conn.fetchrow(
        """SELECT ah.account_id::text AS id, a.is_active FROM account_handles ah JOIN accounts a ON a.id = ah.account_id
           WHERE ah.platform = $1 AND ah.handle_key = $2""", platform, handle)
    if row:
        if not row["is_active"]:
            await conn.execute("UPDATE accounts SET is_active = true, updated_at = now() WHERE id = $1::uuid", row["id"])
        return row["id"]
    account_id = await conn.fetchval("INSERT INTO accounts (platform) VALUES ($1) RETURNING id::text", platform)
    await conn.execute(
        """INSERT INTO account_handles (account_id, platform, handle_key, handle_text, identifier_kind, is_current)
           VALUES ($1::uuid, $2, $3, $4, 'handle', true)""", account_id, platform, handle, handle)
    return account_id


async def _owned_elsewhere(conn: asyncpg.Connection, account_id: str, client_id: str) -> Optional[str]:
    return await conn.fetchval(
        """SELECT c.display_name FROM client_account_roles r JOIN clients c ON c.id = r.client_id
           WHERE r.account_id = $1::uuid AND r.role = 'owned' AND r.is_active AND r.client_id <> $2::uuid""",
        account_id, client_id)


async def link_account(conn: asyncpg.Connection, client_id: str, version: Optional[int], platform: str, handle: str,
                       relation: str, person_id: Optional[str]) -> int:
    if relation not in RELATIONS:
        raise Invalid("Hubungan akun harus own atau competitor.")
    await lock_client(conn, client_id, version)
    account_id = await resolve_account(conn, platform, handle)
    role = RELATIONS[relation]
    if role == "owned":
        other = await _owned_elsewhere(conn, account_id, client_id)
        if other:
            raise Invalid(f"Akun ini sudah tercatat milik {other}. Nonaktifkan di sana dulu.")
    existing = await conn.fetchrow(
        "SELECT id, role, is_active FROM client_account_roles WHERE client_id = $1::uuid AND account_id = $2::uuid",
        client_id, account_id)
    label = f"{platform}:{names.profile_handle(handle)}"
    if existing:
        if existing["role"] == role and existing["is_active"]:
            raise Invalid("Akun ini sudah tertaut ke klien ini.")
        await conn.execute("UPDATE client_account_roles SET role = $2, is_active = true, updated_at = now() WHERE id = $1",
                           existing["id"], role)
        old = {"account": label, "relation": existing["role"], "is_active": existing["is_active"]}
    else:
        await conn.execute(
            "INSERT INTO client_account_roles (client_id, account_id, role) VALUES ($1::uuid, $2::uuid, $3)",
            client_id, account_id, role)
        old = None
    await record_change(conn, entity="account_link", entity_id=account_id, client_id=client_id, field="relation",
                        old=old, new={"account": label, "relation": role, "is_active": True}, person_id=person_id)
    return await bump(conn, client_id)


async def update_account_link(conn: asyncpg.Connection, client_id: str, account_id: str, version: Optional[int],
                              relation: Optional[str], is_active: Optional[bool], person_id: Optional[str]) -> int:
    await lock_client(conn, client_id, version)
    link = await conn.fetchrow(
        """SELECT r.id, r.role, r.is_active, ah.platform, ah.handle_text FROM client_account_roles r
           LEFT JOIN account_handles ah ON ah.account_id = r.account_id AND ah.is_current
           WHERE r.client_id = $1::uuid AND r.account_id = $2::uuid""", client_id, account_id)
    if link is None:
        raise NotFound("Akun tidak tertaut ke klien ini.")
    role = RELATIONS.get(relation, None) if relation is not None else link["role"]
    if relation is not None and role is None:
        raise Invalid("Hubungan akun harus own atau competitor.")
    active = link["is_active"] if is_active is None else bool(is_active)
    if role == link["role"] and active == link["is_active"]:
        return await conn.fetchval("SELECT version FROM clients WHERE id = $1::uuid", client_id)
    if role == "owned" and active:
        other = await _owned_elsewhere(conn, account_id, client_id)
        if other:
            raise Invalid(f"Akun ini sudah tercatat milik {other}. Nonaktifkan di sana dulu.")
    await conn.execute("UPDATE client_account_roles SET role = $2, is_active = $3, updated_at = now() WHERE id = $1",
                       link["id"], role, active)
    label = f"{link['platform']}:{link['handle_text']}"
    await record_change(conn, entity="account_link", entity_id=account_id, client_id=client_id, field="relation",
                        old={"account": label, "relation": link["role"], "is_active": link["is_active"]},
                        new={"account": label, "relation": role, "is_active": active}, person_id=person_id)
    return await bump(conn, client_id)


async def history(conn: asyncpg.Connection, client_id: str, limit: int = 200) -> list:
    rows = await conn.fetch(
        """SELECT r.id, r.entity, r.field, r.old_value, r.new_value, p.display_name AS person, r.at
           FROM registry_changes r LEFT JOIN people p ON p.id = r.person_id
           WHERE r.client_id = $1::uuid ORDER BY r.at DESC, r.id DESC LIMIT $2""", client_id, limit)
    return [{"id": r["id"], "entity": r["entity"], "field": r["field"], "old_value": r["old_value"],
             "new_value": r["new_value"], "person": r["person"] or "Sistem", "at": r["at"].isoformat()} for r in rows]
