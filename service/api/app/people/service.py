"""
People and roles (US5, G-9, G-11, G-16). Every change is logged in
`registry_changes` (entity person / person_email / person_role, FR-013).

Who sees and manages whom:
  - the Owner: everyone;
  - other Managers: People holding an active role in one of their Noktah Brands,
    plus People with NO active role (staff imported from WORKERS, new hires not yet
    given a role) — otherwise a Brand Manager couldn't pick them for a team.
Granting follows `permissions.may_grant`: a Brand Manager grants roles only in their
own Noktah Brand and never brand_manager or owner; only the Owner appoints a BM.

A Person who leaves is never deleted: roles and team assignments end, sign-in stops
(auth.load_caller requires status active), and history keeps their name.
"""
from typing import Any, Dict, List, Optional

import asyncpg

from ..auth import Caller
from ..errors import Conflict, Forbidden, Invalid, NotFound
from ..permissions import ALL_ROLES, Action, Decision, can, may_grant
from ..registry.changes import record_change

PERSON_FIELDS = ("display_name", "jira_account_id", "slack_user_id", "status")


def is_owner(caller: Caller) -> bool:
    return any(a.role == "owner" for a in caller.assignments)


def managed_brands(caller: Caller) -> List[str]:
    return sorted({a.brand for a in caller.assignments
                   if a.brand and can(caller.assignments, Action.MANAGE_PEOPLE, a.brand) is Decision.ALLOW})


async def _roles(conn: asyncpg.Connection, person_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    rows = await conn.fetch(
        """SELECT r.id::text AS id, r.person_id::text AS pid, r.role, b.brand_key
           FROM person_roles r LEFT JOIN noktah_brands b ON b.id = r.noktah_brand_id
           WHERE r.valid_to IS NULL AND r.person_id = ANY($1::uuid[]) ORDER BY r.valid_from""", person_ids)
    out: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r["pid"], []).append({"id": r["id"], "role": r["role"], "noktah_brand": r["brand_key"]})
    return out


def visible(caller: Caller, roles: List[Dict[str, Any]], brands: List[str]) -> bool:
    if is_owner(caller):
        return True
    return not roles or any(r["noktah_brand"] in brands for r in roles)


async def list_people(conn: asyncpg.Connection, caller: Caller, brands: List[str], status: str) -> List[Dict[str, Any]]:
    rows = await conn.fetch(
        """SELECT p.id::text AS id, p.display_name, p.status, p.jira_account_id, p.slack_user_id, p.version,
                  COALESCE(array_agg(e.email ORDER BY e.created_at) FILTER (WHERE e.email IS NOT NULL), '{}') AS emails
           FROM people p LEFT JOIN person_emails e ON e.person_id = p.id
           WHERE ($1 = 'all' OR p.status = $1)
           GROUP BY p.id ORDER BY p.status, p.display_name""", status)
    roles = await _roles(conn, [r["id"] for r in rows])
    return [dict(r) | {"emails": list(r["emails"]), "roles": roles.get(r["id"], [])} for r in rows
            if visible(caller, roles.get(r["id"], []), brands)]


async def get_person(conn: asyncpg.Connection, caller: Caller, brands: List[str], person_id: str) -> Dict[str, Any]:
    row = await conn.fetchrow(
        """SELECT id::text AS id, display_name, status, jira_account_id, slack_user_id, version, left_at
           FROM people WHERE id = $1::uuid""", person_id)
    if row is None:
        raise NotFound("Orang tidak ditemukan.")
    roles = (await _roles(conn, [person_id])).get(person_id, [])
    if not visible(caller, roles, brands):
        raise NotFound("Orang tidak ditemukan.")
    emails = [r["email"] for r in await conn.fetch(
        "SELECT email FROM person_emails WHERE person_id = $1::uuid ORDER BY created_at", person_id)]
    team = await conn.fetch(
        """SELECT c.id::text AS client_id, c.display_name AS client, t.team_role FROM client_team_assignments t
           JOIN clients c ON c.id = t.client_id WHERE t.person_id = $1::uuid AND t.valid_to IS NULL
           ORDER BY c.display_name""", person_id)
    hist = await conn.fetch(
        """SELECT r.id, r.entity, r.field, r.old_value, r.new_value, p.display_name AS person, r.at
           FROM registry_changes r LEFT JOIN people p ON p.id = r.person_id
           WHERE r.entity IN ('person', 'person_email', 'person_role') AND r.entity_id = $1::uuid
           ORDER BY r.at DESC, r.id DESC LIMIT 200""", person_id)
    return dict(row) | {
        "left_at": row["left_at"].isoformat() if row["left_at"] else None, "emails": emails, "roles": roles,
        "team": [dict(t) for t in team],
        "history": [{"id": h["id"], "entity": h["entity"], "field": h["field"], "old_value": h["old_value"],
                     "new_value": h["new_value"], "person": h["person"] or "Sistem", "at": h["at"].isoformat()}
                    for h in hist]}


def require_manage(caller: Caller, roles: List[Dict[str, Any]]) -> None:
    """May the caller edit this Person at all (name, IDs, emails, leaving)?"""
    if is_owner(caller):
        return
    mine = managed_brands(caller)
    if not mine:
        raise Forbidden("Peran Anda tidak bisa mengelola orang.")
    if roles and not any(r["noktah_brand"] in mine for r in roles):
        raise Forbidden("Orang ini ada di Noktah Brand lain.")
    if any(r["role"] in ("owner", "brand_manager") for r in roles):
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


async def create_person(conn: asyncpg.Connection, *, display_name: str, emails: List[str],
                        jira_account_id: Optional[str], slack_user_id: Optional[str], by: Optional[str]) -> str:
    name = " ".join((display_name or "").split())
    if not name:
        raise Invalid("Nama wajib diisi.")
    pid = await conn.fetchval(
        "INSERT INTO people (display_name, jira_account_id, slack_user_id) VALUES ($1, $2, $3) RETURNING id::text",
        name, (jira_account_id or "").strip() or None, (slack_user_id or "").strip() or None)
    await record_change(conn, entity="person", entity_id=pid, field="created", old=None, new={"name": name},
                        person_id=by)
    for e in emails:
        await add_email(conn, pid, e, by)
    return pid


async def lock_person(conn: asyncpg.Connection, person_id: str, version: Optional[int]) -> asyncpg.Record:
    row = await conn.fetchrow("SELECT * FROM people WHERE id = $1::uuid FOR UPDATE", person_id)
    if row is None:
        raise NotFound("Orang tidak ditemukan.")
    if version is not None and version != row["version"]:
        raise Conflict("Data orang ini sudah diubah orang lain. Muat ulang lalu coba lagi.", version=row["version"])
    return row


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
        await record_change(conn, entity="person", entity_id=person_id, field=field, old=current[field], new=value,
                            person_id=by)
    await conn.execute("UPDATE people SET version = version + 1, updated_at = now() WHERE id = $1::uuid", person_id)


async def mark_left(conn: asyncpg.Connection, person_id: str, by: Optional[str]) -> None:
    """Leaving ends every role and team assignment; nothing is deleted."""
    await conn.execute("UPDATE people SET status = 'left', left_at = now() WHERE id = $1::uuid", person_id)
    for r in await conn.fetch(
            """UPDATE person_roles SET valid_to = now() WHERE person_id = $1::uuid AND valid_to IS NULL
               RETURNING id::text AS id, role""", person_id):
        await record_change(conn, entity="person_role", entity_id=person_id, field=r["role"], old="active",
                            new="ended (keluar)", person_id=by)
    for t in await conn.fetch(
            """UPDATE client_team_assignments SET valid_to = now() WHERE person_id = $1::uuid AND valid_to IS NULL
               RETURNING client_id::text AS client_id, team_role""", person_id):
        name = await conn.fetchval("SELECT display_name FROM people WHERE id = $1::uuid", person_id)
        await record_change(conn, entity="team", entity_id=t["client_id"], client_id=t["client_id"],
                            field=t["team_role"], old=name, new=None, person_id=by)
        await conn.execute("UPDATE clients SET version = version + 1 WHERE id = $1::uuid", t["client_id"])


async def grant_role(conn: asyncpg.Connection, caller: Caller, person_id: str, role: str, brand: Optional[str]) -> str:
    if role not in ALL_ROLES:
        raise Invalid("Peran tidak dikenal.")
    brand = None if role == "owner" else brand
    if role != "owner" and not brand:
        raise Invalid("Pilih Noktah Brand untuk peran ini.")
    if not may_grant(caller.assignments, role, brand):
        raise Forbidden("Peran Anda tidak bisa memberi peran ini." if role not in ("brand_manager", "owner")
                        else "Hanya Owner yang bisa mengangkat Brand Manager atau Owner.")
    person = await lock_person(conn, person_id, None)
    if person["status"] != "active":
        raise Invalid("Orang ini sudah keluar.")
    brand_id = None
    if brand:
        brand_id = await conn.fetchval("SELECT id FROM noktah_brands WHERE brand_key = $1", brand)
        if brand_id is None:
            raise Invalid("Noktah Brand tidak dikenal.")
    if role == "brand_manager":
        holder = await conn.fetchval(
            """SELECT p.display_name FROM person_roles r JOIN people p ON p.id = r.person_id
               WHERE r.role = 'brand_manager' AND r.noktah_brand_id = $1 AND r.valid_to IS NULL""", brand_id)
        if holder:
            raise Invalid(f"{brand} sudah punya Brand Manager: {holder}. Akhiri perannya dulu.")
    try:
        role_id = await conn.fetchval(
            """INSERT INTO person_roles (person_id, role, noktah_brand_id, granted_by)
               VALUES ($1::uuid, $2, $3, $4::uuid) RETURNING id::text""", person_id, role, brand_id, caller.person_id)
    except asyncpg.UniqueViolationError:
        raise Invalid("Orang ini sudah memegang peran itu.")
    await record_change(conn, entity="person_role", entity_id=person_id, field=role, old=None,
                        new={"role": role, "noktah_brand": brand}, person_id=caller.person_id)
    return role_id


async def end_role(conn: asyncpg.Connection, caller: Caller, person_id: str, role_id: str) -> None:
    row = await conn.fetchrow(
        """SELECT r.role, b.brand_key FROM person_roles r LEFT JOIN noktah_brands b ON b.id = r.noktah_brand_id
           WHERE r.id = $1::uuid AND r.person_id = $2::uuid AND r.valid_to IS NULL""", role_id, person_id)
    if row is None:
        raise NotFound("Peran tidak ditemukan atau sudah berakhir.")
    if not may_grant(caller.assignments, row["role"], row["brand_key"]):
        raise Forbidden("Peran Anda tidak bisa mengakhiri peran ini.")
    if row["role"] == "owner":
        owners = await conn.fetchval("SELECT count(*) FROM person_roles WHERE role = 'owner' AND valid_to IS NULL")
        if owners <= 1:
            raise Invalid("Owner terakhir tidak bisa diakhiri.")
    await conn.execute("UPDATE person_roles SET valid_to = now() WHERE id = $1::uuid", role_id)
    await record_change(conn, entity="person_role", entity_id=person_id, field=row["role"],
                        old={"role": row["role"], "noktah_brand": row["brand_key"]}, new=None, person_id=caller.person_id)


async def remove_email(conn: asyncpg.Connection, person_id: str, email: str, by: Optional[str]) -> None:
    gone = await conn.fetchval("DELETE FROM person_emails WHERE person_id = $1::uuid AND email = $2 RETURNING email",
                               person_id, email.strip().lower())
    if gone is None:
        raise NotFound("Email tidak ditemukan pada orang ini.")
    await record_change(conn, entity="person_email", entity_id=person_id, field="email", old=gone, new=None,
                        person_id=by)


def role_key(role: str, brand: Optional[str]) -> tuple:
    return role, None if role == "owner" else brand


async def save_person(conn: asyncpg.Connection, caller: Caller, person_id: str, *, version: int,
                      fields: Dict[str, Any], emails: List[str], roles: List[tuple]) -> None:
    """The profile form's one Save: details, emails and roles in one transaction.

    Only the differences are applied, each through the rule its single-change route
    uses (require_manage for details and emails, may_grant for each role), so one
    refused role or a taken email rolls the whole save back. Roles are ended before
    new ones are granted, so replacing a Brand Manager works in one save.
    """
    current = await lock_person(conn, person_id, version)
    if current["status"] != "active":
        raise Invalid("Orang ini sudah keluar.")
    have_roles = (await _roles(conn, [person_id])).get(person_id, [])

    details = {k: v for k, v in fields.items() if k in ("display_name", "jira_account_id", "slack_user_id")}
    have_emails = [r["email"] for r in await conn.fetch(
        "SELECT email FROM person_emails WHERE person_id = $1::uuid", person_id)]
    want_emails = list(dict.fromkeys(clean_email(e) for e in emails))
    details_change = any(
        (" ".join((v or "").split()) if k == "display_name" else (v or "").strip() or None) != current[k]
        for k, v in details.items())
    if details_change or set(want_emails) != set(have_emails):
        require_manage(caller, have_roles)

    changed = False
    if details_change:
        await update_person(conn, person_id, version, details, caller.person_id)
    for e in have_emails:
        if e not in want_emails:
            await remove_email(conn, person_id, e, caller.person_id)
            changed = True
    for e in want_emails:
        if e not in have_emails:
            await add_email(conn, person_id, e, caller.person_id)
            changed = True

    want_roles = {role_key(r, b) for r, b in roles}
    for r in have_roles:
        if (r["role"], r["noktah_brand"]) not in want_roles:
            await end_role(conn, caller, person_id, r["id"])
            changed = True
    have_keys = {(r["role"], r["noktah_brand"]) for r in have_roles}
    for role, brand in sorted(want_roles - have_keys, key=lambda k: (k[0], k[1] or "")):
        await grant_role(conn, caller, person_id, role, brand)
        changed = True
    if changed and not details_change:
        await conn.execute("UPDATE people SET version = version + 1, updated_at = now() WHERE id = $1::uuid",
                           person_id)
