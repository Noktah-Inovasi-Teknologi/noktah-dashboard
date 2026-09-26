"""
People: their details, emails, Units, roles and permissions (US5, G-11, G-16,
migration 012). Every change is logged in `registry_changes` (entity person /
person_email / person_unit / person_role / person_permission, FR-013).

Who sees and manages whom:
  - the Owner, and anyone in the Noktah group: everyone;
  - others: People in one of their Units, plus People in no Unit yet (new hires,
    staff imported from WORKERS), who would otherwise be unreachable.
Changing a Person needs manage_people and the rules in `permissions`: roles only
in Units within reach and never Owner or Brand Manager (the Owner's alone), and
only permissions the manager holds. People holding Owner or Brand Manager are
changed by the Owner only.

A Person who leaves is never deleted: roles, team assignments and permissions end,
sign-in stops (auth.load_caller requires status active), and history keeps their name.
"""
from datetime import date
from typing import Any, Dict, Iterable, List, Optional

import asyncpg

from ..auth import Caller
from ..errors import Conflict, Forbidden, Invalid, NotFound
from ..permissions import (OWNER_ONLY_ROLES, PERMISSIONS, in_scope, is_owner, may_give_permission, may_grant,
                           may_manage, may_set_unit, normalize_permissions)
from ..registry.changes import record_change
from . import catalog

PERSON_FIELDS = ("display_name", "jira_account_id", "slack_user_id", "status", "started_on")


async def _roles(conn: asyncpg.Connection, person_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    rows = await conn.fetch(
        """SELECT r.id::text AS id, r.person_id::text AS pid, r.role, b.brand_key
           FROM person_roles r JOIN noktah_brands b ON b.id = r.noktah_brand_id
           LEFT JOIN unit_roles u ON u.noktah_brand_id = r.noktah_brand_id AND u.role = r.role
           WHERE r.valid_to IS NULL AND r.person_id = ANY($1::uuid[])
           ORDER BY b.kind DESC, b.brand_key, u.sort_order""", person_ids)
    out: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r["pid"], []).append({"id": r["id"], "role": r["role"], "noktah_brand": r["brand_key"]})
    return out


async def _units(conn: asyncpg.Connection, person_ids: List[str]) -> Dict[str, List[str]]:
    rows = await conn.fetch(
        """SELECT u.person_id::text AS pid, b.brand_key FROM person_units u JOIN noktah_brands b ON b.id = u.noktah_brand_id
           WHERE u.person_id = ANY($1::uuid[]) ORDER BY b.kind DESC, b.brand_key""", person_ids)
    out: Dict[str, List[str]] = {}
    for r in rows:
        out.setdefault(r["pid"], []).append(r["brand_key"])
    return out


async def _permissions(conn: asyncpg.Connection, person_ids: List[str]) -> Dict[str, List[str]]:
    rows = await conn.fetch(
        "SELECT person_id::text AS pid, permission FROM person_permissions WHERE person_id = ANY($1::uuid[])",
        person_ids)
    out: Dict[str, List[str]] = {}
    for r in rows:
        out.setdefault(r["pid"], []).append(r["permission"])
    return {pid: [p for p in PERMISSIONS if p in perms] for pid, perms in out.items()}


def visible(caller: Caller, units: List[str]) -> bool:
    return not units or any(in_scope(caller.access, u) for u in units)


async def list_people(conn: asyncpg.Connection, caller: Caller, status: str) -> List[Dict[str, Any]]:
    rows = await conn.fetch(
        """SELECT p.id::text AS id, p.display_name, p.status, p.jira_account_id, p.slack_user_id, p.version,
                  COALESCE(array_agg(e.email ORDER BY e.created_at) FILTER (WHERE e.email IS NOT NULL), '{}') AS emails
           FROM people p LEFT JOIN person_emails e ON e.person_id = p.id
           WHERE ($1 = 'all' OR p.status = $1)
           GROUP BY p.id ORDER BY p.status, p.display_name""", status)
    ids = [r["id"] for r in rows]
    roles, units, perms = await _roles(conn, ids), await _units(conn, ids), await _permissions(conn, ids)
    return [dict(r) | {"emails": list(r["emails"]), "units": units.get(r["id"], []), "roles": roles.get(r["id"], []),
                       "permissions": perms.get(r["id"], [])}
            for r in rows if visible(caller, units.get(r["id"], []))]


async def get_person(conn: asyncpg.Connection, caller: Caller, person_id: str) -> Dict[str, Any]:
    row = await conn.fetchrow(
        """SELECT id::text AS id, display_name, status, jira_account_id, slack_user_id, version, left_at, started_on
           FROM people WHERE id = $1::uuid""", person_id)
    if row is None:
        raise NotFound("Orang tidak ditemukan.")
    units = (await _units(conn, [person_id])).get(person_id, [])
    if not visible(caller, units):
        raise NotFound("Orang tidak ditemukan.")
    roles = (await _roles(conn, [person_id])).get(person_id, [])
    perms = (await _permissions(conn, [person_id])).get(person_id, [])
    emails = [r["email"] for r in await conn.fetch(
        "SELECT email FROM person_emails WHERE person_id = $1::uuid ORDER BY created_at", person_id)]
    team = await conn.fetch(
        """SELECT c.id::text AS client_id, c.display_name AS client, t.team_role, b.brand_key AS brand
           FROM client_team_assignments t JOIN clients c ON c.id = t.client_id
           LEFT JOIN noktah_brands b ON b.id = c.noktah_brand_id
           WHERE t.person_id = $1::uuid AND t.valid_to IS NULL
           ORDER BY c.display_name""", person_id)
    hist = await conn.fetch(
        """SELECT r.id, r.entity, r.field, r.old_value, r.new_value, p.display_name AS person, r.at
           FROM registry_changes r LEFT JOIN people p ON p.id = r.person_id
           WHERE r.entity IN ('person', 'person_email', 'person_unit', 'person_role', 'person_permission')
             AND r.entity_id = $1::uuid
           ORDER BY r.at DESC, r.id DESC LIMIT 200""", person_id)
    return dict(row) | {
        "left_at": row["left_at"].isoformat() if row["left_at"] else None,
        "started_on": row["started_on"].isoformat() if row["started_on"] else None, "emails": emails,
        "units": units, "roles": roles, "permissions": perms,
        "team": [dict(t) for t in team],
        "history": [{"id": h["id"], "entity": h["entity"], "field": h["field"], "old_value": h["old_value"],
                     "new_value": h["new_value"], "person": h["person"] or "Sistem", "at": h["at"].isoformat()}
                    for h in hist]}


def require_manage(caller: Caller, units: List[str], roles: List[Dict[str, Any]]) -> None:
    """May the caller change this Person at all?"""
    if is_owner(caller.access):
        return
    if not may_manage(caller.access):
        raise Forbidden("Anda tidak punya izin mengelola orang.")
    if units and not any(in_scope(caller.access, u) for u in units):
        raise Forbidden("Orang ini di luar unit yang Anda kelola.")
    if any(r["role"] in OWNER_ONLY_ROLES for r in roles):
        raise Forbidden("Hanya Owner yang bisa mengubah data Brand Manager atau Owner.")


def clean_email(email: str) -> str:
    email = (email or "").strip().lower()
    if "@" not in email or " " in email or len(email) < 5:
        raise Invalid("Alamat email tidak valid.")
    return email


async def add_email(conn: asyncpg.Connection, person_id: str, email: str, by: Optional[str]) -> None:
    email = clean_email(email)
    owner = await conn.fetchrow(
        """SELECT p.id::text AS id, p.display_name FROM person_emails e JOIN people p ON p.id = e.person_id
           WHERE e.email = $1""", email)
    if owner:
        if owner["id"] == person_id:
            raise Invalid("Email ini sudah tercatat untuk orang ini.")
        raise Invalid(f"Email {email} sudah dipakai {owner['display_name']}.", person_id=owner["id"],
                      person=owner["display_name"])
    await conn.execute("INSERT INTO person_emails (person_id, email) VALUES ($1::uuid, $2)", person_id, email)
    await record_change(conn, entity="person_email", entity_id=person_id, field="email", old=None, new=email,
                        person_id=by)


async def remove_email(conn: asyncpg.Connection, person_id: str, email: str, by: Optional[str]) -> None:
    gone = await conn.fetchval("DELETE FROM person_emails WHERE person_id = $1::uuid AND email = $2 RETURNING email",
                               person_id, email.strip().lower())
    if gone is None:
        raise NotFound("Email tidak ditemukan pada orang ini.")
    await record_change(conn, entity="person_email", entity_id=person_id, field="email", old=gone, new=None,
                        person_id=by)


async def create_person(conn: asyncpg.Connection, *, display_name: str, jira_account_id: Optional[str],
                        slack_user_id: Optional[str], by: Optional[str]) -> str:
    name = " ".join((display_name or "").split())
    if not name:
        raise Invalid("Nama wajib diisi.")
    pid = await conn.fetchval(
        "INSERT INTO people (display_name, jira_account_id, slack_user_id) VALUES ($1, $2, $3) RETURNING id::text",
        name, (jira_account_id or "").strip() or None, (slack_user_id or "").strip() or None)
    await record_change(conn, entity="person", entity_id=pid, field="created", old=None, new={"name": name},
                        person_id=by)
    return pid


async def lock_person(conn: asyncpg.Connection, person_id: str, version: Optional[int]) -> asyncpg.Record:
    row = await conn.fetchrow("SELECT * FROM people WHERE id = $1::uuid FOR UPDATE", person_id)
    if row is None:
        raise NotFound("Orang tidak ditemukan.")
    if version is not None and version != row["version"]:
        raise Conflict("Data orang ini sudah diubah orang lain. Muat ulang lalu coba lagi.", version=row["version"])
    return row


def _jsonable(value: Any) -> Any:
    return value.isoformat() if isinstance(value, date) else value


async def update_person(conn: asyncpg.Connection, person_id: str, version: Optional[int], changes: Dict[str, Any],
                        by: Optional[str]) -> None:
    current = await lock_person(conn, person_id, version)
    for field, value in changes.items():
        if field not in PERSON_FIELDS:
            raise Invalid(f"Kolom {field} tidak bisa diubah.")
        if field == "display_name":
            value = " ".join((value or "").split())
            if not value:
                raise Invalid("Nama wajib diisi.")
        elif field == "status":
            if value not in ("active", "left"):
                raise Invalid("Status harus active atau left.")
        elif field == "started_on":
            # "Mulai bekerja": the start of the Incentive Framework's 30-day adaptation (spec 009 G-27)
            if isinstance(value, str):
                try:
                    value = date.fromisoformat(value) if value.strip() else None
                except ValueError:
                    raise Invalid("Tanggal mulai bekerja tidak valid.")
        else:
            value = (value or "").strip() or None
        if value == current[field]:
            continue
        if field == "status" and value == "left":
            await mark_left(conn, person_id, by)
        elif field == "status":
            await conn.execute("UPDATE people SET status = 'active', left_at = NULL WHERE id = $1::uuid", person_id)
        else:
            await conn.execute(f"UPDATE people SET {field} = $2 WHERE id = $1::uuid", person_id, value)
        await record_change(conn, entity="person", entity_id=person_id, field=field,
                            old=_jsonable(current[field]), new=_jsonable(value), person_id=by)
    await conn.execute("UPDATE people SET version = version + 1, updated_at = now() WHERE id = $1::uuid", person_id)


async def mark_left(conn: asyncpg.Connection, person_id: str, by: Optional[str]) -> None:
    """Leaving ends every role, team assignment and permission; nothing else is deleted."""
    await conn.execute("UPDATE people SET status = 'left', left_at = now() WHERE id = $1::uuid", person_id)
    for r in await conn.fetch(
            """UPDATE person_roles SET valid_to = now() WHERE person_id = $1::uuid AND valid_to IS NULL
               RETURNING id::text AS id, role""", person_id):
        await record_change(conn, entity="person_role", entity_id=person_id, field=r["role"], old="active",
                            new="ended (keluar)", person_id=by)
    for p in await conn.fetch(
            "DELETE FROM person_permissions WHERE person_id = $1::uuid RETURNING permission", person_id):
        await record_change(conn, entity="person_permission", entity_id=person_id, field=p["permission"],
                            old=True, new=None, person_id=by)
    for t in await conn.fetch(
            """UPDATE client_team_assignments SET valid_to = now() WHERE person_id = $1::uuid AND valid_to IS NULL
               RETURNING client_id::text AS client_id, team_role""", person_id):
        name = await conn.fetchval("SELECT display_name FROM people WHERE id = $1::uuid", person_id)
        await record_change(conn, entity="team", entity_id=t["client_id"], client_id=t["client_id"],
                            field=t["team_role"], old=name, new=None, person_id=by)
        await conn.execute("UPDATE clients SET version = version + 1 WHERE id = $1::uuid", t["client_id"])


# ── Units ─────────────────────────────────────────────────────────────────────

async def add_unit(conn: asyncpg.Connection, person_id: str, unit: str, by: Optional[str]) -> None:
    """Put a Person in a Unit. `by` None is the system (a migration or the import)."""
    uid = await catalog.unit_id(conn, unit)
    if uid is None:
        raise Invalid("Unit tidak dikenal.")
    added = await conn.fetchval(
        """INSERT INTO person_units (person_id, noktah_brand_id, added_by) VALUES ($1::uuid, $2::uuid, $3::uuid)
           ON CONFLICT DO NOTHING RETURNING 1""", person_id, uid, by)
    if added:
        await record_change(conn, entity="person_unit", entity_id=person_id, field=unit, old=None, new=unit,
                            person_id=by)


async def remove_unit(conn: asyncpg.Connection, person_id: str, unit: str, by: Optional[str]) -> None:
    uid = await catalog.unit_id(conn, unit)
    if await conn.fetchval("SELECT 1 FROM person_roles WHERE person_id = $1::uuid AND noktah_brand_id = $2::uuid "
                           "AND valid_to IS NULL", person_id, uid):
        raise Invalid("Akhiri dulu peran orang ini di unit tersebut.")
    await conn.execute("DELETE FROM person_units WHERE person_id = $1::uuid AND noktah_brand_id = $2::uuid",
                       person_id, uid)
    await record_change(conn, entity="person_unit", entity_id=person_id, field=unit, old=unit, new=None,
                        person_id=by)


# ── Roles ─────────────────────────────────────────────────────────────────────

async def insert_role(conn: asyncpg.Connection, person_id: str, role: str, unit: str, by: Optional[str]) -> str:
    """Record a role, no permission check: callers check (grant_role) or are the system."""
    if not await catalog.role_exists(conn, role, unit):
        raise Invalid("Peran ini tidak ada di unit tersebut.")
    uid = await catalog.unit_id(conn, unit)
    if not await conn.fetchval("SELECT 1 FROM person_units WHERE person_id = $1::uuid AND noktah_brand_id = $2::uuid",
                               person_id, uid):
        await add_unit(conn, person_id, unit, by)
    if role == "brand_manager":
        holder = await conn.fetchval(
            """SELECT p.display_name FROM person_roles r JOIN people p ON p.id = r.person_id
               WHERE r.role = 'brand_manager' AND r.noktah_brand_id = $1::uuid AND r.valid_to IS NULL
                 AND r.person_id <> $2::uuid""", uid, person_id)
        if holder:
            raise Invalid(f"{unit.capitalize()} sudah punya Brand Manager: {holder}. Akhiri perannya dulu.")
    try:
        role_id = await conn.fetchval(
            """INSERT INTO person_roles (person_id, role, noktah_brand_id, granted_by)
               VALUES ($1::uuid, $2, $3::uuid, $4::uuid) RETURNING id::text""", person_id, role, uid, by)
    except asyncpg.UniqueViolationError:
        raise Invalid("Orang ini sudah memegang peran itu.")
    await record_change(conn, entity="person_role", entity_id=person_id, field=role, old=None,
                        new={"role": role, "noktah_brand": unit}, person_id=by)
    return role_id


def _refused_role(caller: Caller, role: str) -> str:
    if role in OWNER_ONLY_ROLES:
        return "Hanya Owner yang bisa mengangkat atau mengakhiri Brand Manager atau Owner."
    if not may_manage(caller.access):
        return "Anda tidak punya izin mengelola orang."
    return "Peran ini di luar unit yang Anda kelola."


async def ensure_role(conn: asyncpg.Connection, person_id: str, role: str, unit: str, by: Optional[str]) -> None:
    """The system's grant (the Registry import): a no-op when the role is already held."""
    if not await conn.fetchval(
            """SELECT 1 FROM person_roles r JOIN noktah_brands b ON b.id = r.noktah_brand_id
               WHERE r.person_id = $1::uuid AND r.role = $2 AND b.brand_key = $3 AND r.valid_to IS NULL""",
            person_id, role, unit):
        await insert_role(conn, person_id, role, unit, by)


async def grant_role(conn: asyncpg.Connection, caller: Caller, person_id: str, role: str, unit: Optional[str]) -> str:
    if not unit or not await catalog.role_exists(conn, role, unit):
        raise Invalid("Peran ini tidak ada di unit tersebut.")
    if not may_grant(caller.access, role, unit):
        raise Forbidden(_refused_role(caller, role))
    person = await lock_person(conn, person_id, None)
    if person["status"] != "active":
        raise Invalid("Orang ini sudah keluar.")
    return await insert_role(conn, person_id, role, unit, caller.person_id)


async def end_role(conn: asyncpg.Connection, caller: Caller, person_id: str, role_id: str) -> None:
    row = await conn.fetchrow(
        """SELECT r.role, b.brand_key FROM person_roles r JOIN noktah_brands b ON b.id = r.noktah_brand_id
           WHERE r.id = $1::uuid AND r.person_id = $2::uuid AND r.valid_to IS NULL""", role_id, person_id)
    if row is None:
        raise NotFound("Peran tidak ditemukan atau sudah berakhir.")
    if not may_grant(caller.access, row["role"], row["brand_key"]):
        raise Forbidden(_refused_role(caller, row["role"]))
    if row["role"] == "owner":
        owners = await conn.fetchval("SELECT count(*) FROM person_roles WHERE role = 'owner' AND valid_to IS NULL")
        if owners <= 1:
            raise Invalid("Owner terakhir tidak bisa diakhiri.")
    await conn.execute("UPDATE person_roles SET valid_to = now() WHERE id = $1::uuid", role_id)
    await record_change(conn, entity="person_role", entity_id=person_id, field=row["role"],
                        old={"role": row["role"], "noktah_brand": row["brand_key"]}, new=None, person_id=caller.person_id)
    await _leave_teams(conn, person_id, row["role"], row["brand_key"], caller.person_id)


async def _leave_teams(conn: asyncpg.Connection, person_id: str, role: str, brand: str, by: Optional[str]) -> None:
    """A team slot needs its role: without it, the Person leaves that slot on the brand's Clients."""
    name = await conn.fetchval("SELECT display_name FROM people WHERE id = $1::uuid", person_id)
    for t in await conn.fetch(
            """UPDATE client_team_assignments t SET valid_to = now()
               FROM clients c JOIN noktah_brands b ON b.id = c.noktah_brand_id
               WHERE c.id = t.client_id AND b.brand_key = $3 AND t.person_id = $1::uuid AND t.team_role = $2
                 AND t.valid_to IS NULL
               RETURNING t.client_id::text AS client_id""", person_id, role, brand):
        await record_change(conn, entity="team", entity_id=t["client_id"], client_id=t["client_id"], field=role,
                            old=name, new=None, person_id=by)
        await conn.execute("UPDATE clients SET version = version + 1 WHERE id = $1::uuid", t["client_id"])


# ── Permissions ───────────────────────────────────────────────────────────────

async def set_permission(conn: asyncpg.Connection, person_id: str, permission: str, on: bool,
                         by: Optional[str]) -> None:
    if on:
        await conn.execute(
            """INSERT INTO person_permissions (person_id, permission, granted_by) VALUES ($1::uuid, $2, $3::uuid)
               ON CONFLICT DO NOTHING""", person_id, permission, by)
    else:
        await conn.execute("DELETE FROM person_permissions WHERE person_id = $1::uuid AND permission = $2",
                           person_id, permission)
    await record_change(conn, entity="person_permission", entity_id=person_id, field=permission,
                        old=None if on else True, new=True if on else None, person_id=by)


# ── The profile form's one Save ───────────────────────────────────────────────

def role_key(role: str, unit: Optional[str]) -> tuple:
    return role, unit


async def save_person(conn: asyncpg.Connection, caller: Caller, person_id: str, *, version: int,
                      fields: Dict[str, Any], emails: List[str], units: Iterable[str], roles: List[tuple],
                      permissions: Iterable[str]) -> None:
    """Details, emails, Units, roles and permissions in one transaction.

    Only the differences are applied, each by its own rule, so one refused role, a
    permission the manager doesn't hold, or a taken email rolls the whole save back.
    Units are added first and roles ended before Units are removed, so moving
    someone between Units, or replacing a Brand Manager, works in one save.
    """
    current = await lock_person(conn, person_id, version)
    if current["status"] != "active":
        raise Invalid("Orang ini sudah keluar.")
    have_units = (await _units(conn, [person_id])).get(person_id, [])
    have_roles = (await _roles(conn, [person_id])).get(person_id, [])
    have_perms = set((await _permissions(conn, [person_id])).get(person_id, []))

    details = {k: v for k, v in fields.items() if k in ("display_name", "jira_account_id", "slack_user_id")}
    have_emails = [r["email"] for r in await conn.fetch(
        "SELECT email FROM person_emails WHERE person_id = $1::uuid", person_id)]
    want_emails = list(dict.fromkeys(clean_email(e) for e in emails))
    want_units = list(dict.fromkeys(units))
    want_roles = {role_key(r, u) for r, u in roles}
    want_perms = normalize_permissions(permissions)
    unknown = want_perms - set(PERMISSIONS)
    if unknown:
        raise Invalid(f"Izin tidak dikenal: {', '.join(sorted(unknown))}.")
    for role, unit in want_roles:
        if unit not in want_units:
            raise Invalid("Setiap peran harus dari unit orang ini. Tambahkan unitnya dulu.")
    details_change = any(
        (" ".join((v or "").split()) if k == "display_name" else (v or "").strip() or None) != current[k]
        for k, v in details.items())
    have_role_keys = {(r["role"], r["noktah_brand"]) for r in have_roles}
    if (details_change or set(want_emails) != set(have_emails) or set(want_units) != set(have_units)
            or want_roles != have_role_keys or want_perms != have_perms):
        require_manage(caller, have_units, have_roles)
    by = caller.person_id

    if details_change:
        await update_person(conn, person_id, version, details, by)
    changed = False
    for e in have_emails:
        if e not in want_emails:
            await remove_email(conn, person_id, e, by)
            changed = True
    for e in want_emails:
        if e not in have_emails:
            await add_email(conn, person_id, e, by)
            changed = True

    for u in want_units:
        if u not in have_units:
            if not may_set_unit(caller.access, u):
                raise Forbidden(f"Anda tidak bisa menambahkan orang ke unit {u.capitalize()}.")
            await add_unit(conn, person_id, u, by)
            changed = True
    for r in have_roles:
        if (r["role"], r["noktah_brand"]) not in want_roles:
            await end_role(conn, caller, person_id, r["id"])
            changed = True
    for u in have_units:
        if u not in want_units:
            if not may_set_unit(caller.access, u):
                raise Forbidden(f"Anda tidak bisa melepas orang dari unit {u.capitalize()}.")
            await remove_unit(conn, person_id, u, by)
            changed = True
    for role, unit in sorted(want_roles - have_role_keys):
        await grant_role(conn, caller, person_id, role, unit)
        changed = True

    for p in PERMISSIONS:
        if (p in want_perms) != (p in have_perms):
            if not may_give_permission(caller.access, p):
                raise Forbidden("Anda hanya bisa mengatur izin yang Anda punya sendiri.")
            await set_permission(conn, person_id, p, p in want_perms, by)
            changed = True

    if changed and not details_change:
        await conn.execute("UPDATE people SET version = version + 1, updated_at = now() WHERE id = $1::uuid",
                           person_id)
