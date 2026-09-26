"""
Content Plans in the database (spec 009 US1/US2): scans, Greenlights, status, and the
Registry facts a plan needs (team, component, quota). Eskala only (G-39).
"""
from datetime import date
from typing import Any, Dict, List, Optional

import asyncpg

from ..errors import NotFound
from . import plans, watcher

ESKALA = "eskala"
TEAM_QA_ROLES = ("quality_assurance", "qc")


def month_of(value: str) -> date:
    """'2026-10' → date(2026, 10, 1)."""
    try:
        y, m = str(value)[:7].split("-")
        return date(int(y), int(m), 1)
    except (ValueError, TypeError):
        from ..errors import Invalid
        raise Invalid("Bulan harus berformat YYYY-MM.")


async def eskala_clients(conn: asyncpg.Connection, client_ids: Optional[List[str]] = None) -> List[asyncpg.Record]:
    return await conn.fetch(
        """SELECT c.id::text AS id, c.display_name, c.content_plan_folder_id, c.jira_component_id,
                  c.quota_post, c.quota_story, c.quota_short_video, c.status
           FROM clients c JOIN noktah_brands b ON b.id = c.noktah_brand_id
           WHERE b.brand_key = $1 AND c.status = 'active'
             AND ($2::uuid[] IS NULL OR c.id = ANY($2::uuid[]))
           ORDER BY c.display_name""", ESKALA, client_ids)


async def eskala_client(conn: asyncpg.Connection, client_id: str) -> asyncpg.Record:
    row = await conn.fetchrow(
        """SELECT c.id::text AS id, c.display_name, c.content_plan_folder_id, c.jira_component_id,
                  c.quota_post, c.quota_story, c.quota_short_video, c.status
           FROM clients c JOIN noktah_brands b ON b.id = c.noktah_brand_id
           WHERE b.brand_key = $1 AND c.id = $2::uuid""", ESKALA, client_id)
    if row is None:
        raise NotFound("Klien tidak ditemukan.")
    return row


async def team(conn: asyncpg.Connection, client_id: str, at: Optional[Any] = None) -> Dict[str, Dict[str, Any]]:
    """The Client's Team by role (active now, or at `at`), with each Person's Jira account."""
    rows = await conn.fetch(
        """SELECT t.team_role, p.id::text AS person_id, p.display_name, p.jira_account_id
           FROM client_team_assignments t JOIN people p ON p.id = t.person_id
           WHERE t.client_id = $1::uuid
             AND ($2::timestamptz IS NULL AND t.valid_to IS NULL
                  OR $2::timestamptz IS NOT NULL AND t.valid_from <= $2 AND (t.valid_to IS NULL OR t.valid_to > $2))""",
        client_id, at)
    out = {}
    for r in rows:
        role = "quality_assurance" if r["team_role"] in TEAM_QA_ROLES else r["team_role"]
        out[role] = dict(r)
    return out


def quota(client: asyncpg.Record) -> Dict[str, Optional[int]]:
    return {"Post": client["quota_post"], "Story": client["quota_story"], "Short Video": client["quota_short_video"]}


async def upsert_scan(conn: asyncpg.Connection, client_id: str, month: date, body: Dict[str, Any]) -> Dict[str, Any]:
    """Store one scan; run the watcher on it. Returns {plan_id, previous_rows}."""
    previous = await conn.fetchrow(
        "SELECT id::text AS id, rows FROM content_plans WHERE client_id = $1::uuid AND month = $2", client_id, month)
    rows = body.get("rows") or []
    fp = plans.plan_fingerprint(rows) if body["state"] == "found" else None
    plan_id = await conn.fetchval(
        """INSERT INTO content_plans (client_id, month, state, drive_file_id, file_name, tab_name, rows, fingerprint,
                                      problem, scanned_at)
           VALUES ($1::uuid, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, now())
           ON CONFLICT (client_id, month) DO UPDATE SET state = EXCLUDED.state,
               drive_file_id = EXCLUDED.drive_file_id, file_name = EXCLUDED.file_name, tab_name = EXCLUDED.tab_name,
               rows = EXCLUDED.rows, fingerprint = EXCLUDED.fingerprint, problem = EXCLUDED.problem,
               scanned_at = now()
           RETURNING id::text""",
        client_id, month, body["state"], body.get("drive_file_id"), body.get("file_name"), body.get("tab_name"),
        rows, fp, body.get("problem"))
    if body["state"] == "found":
        issues = [dict(r) for r in await conn.fetch(
            """SELECT issue_key, fingerprint, last_fingerprint, cells FROM content_plan_issues
               WHERE plan_id = $1::uuid""", plan_id)]
        if issues:
            findings = watcher.inspect(rows, (previous["rows"] if previous else []) or [], issues)
            await watcher.apply(conn, plan_id, body.get("file_name") or "", findings)
    return {"plan_id": plan_id}


async def load_plan(conn: asyncpg.Connection, plan_id: str) -> asyncpg.Record:
    row = await conn.fetchrow(
        """SELECT p.id::text AS id, p.client_id::text AS client_id, p.month, p.state, p.drive_file_id, p.file_name,
                  p.tab_name, p.rows, p.fingerprint, p.problem, p.scanned_at, c.display_name AS client_name
           FROM content_plans p JOIN clients c ON c.id = p.client_id
           JOIN noktah_brands b ON b.id = c.noktah_brand_id
           WHERE p.id = $1::uuid AND b.brand_key = $2""", plan_id, ESKALA)
    if row is None:
        raise NotFound("Content Plan tidak ditemukan.")
    return row


async def latest_greenlight(conn: asyncpg.Connection, plan_id: str) -> Optional[asyncpg.Record]:
    return await conn.fetchrow(
        """SELECT g.fingerprint, g.at, p.display_name AS by FROM content_plan_greenlights g
           JOIN people p ON p.id = g.person_id
           WHERE g.plan_id = $1::uuid ORDER BY g.at DESC LIMIT 1""", plan_id)


async def issue_rows(conn: asyncpg.Connection, plan_id: str) -> Dict[int, str]:
    return {r["row_number"]: r["issue_key"] for r in await conn.fetch(
        "SELECT row_number, issue_key FROM content_plan_issues WHERE plan_id = $1::uuid", plan_id)}


def rows_without_issue(rows: List[Dict[str, Any]], made: Dict[int, str]) -> List[Dict[str, Any]]:
    """Rows still to create: content rows with no Key and no issue recorded for their row."""
    keys_made = set(made.values())
    return [r for r in plans.content_rows(rows)
            if not plans.key_of(r["cells"]) and r["row_number"] not in made
            and plans.key_of(r["cells"]) not in keys_made]


async def open_flags(conn: asyncpg.Connection, plan_id: str) -> List[asyncpg.Record]:
    return await conn.fetch(
        """SELECT id::text AS id, kind, issue_key, row_number, changes, detected_at, comment_state, commented_at
           FROM content_plan_flags WHERE plan_id = $1::uuid AND resolved_at IS NULL
           ORDER BY detected_at""", plan_id)


def status_of(plan: asyncpg.Record, gl: Optional[asyncpg.Record], made: Dict[int, str],
              flags: List[asyncpg.Record]) -> str:
    if plan["state"] != "found":
        return plan["state"]
    rows = plan["rows"] or []
    remaining = rows_without_issue(rows, made)
    greenlit = gl is not None and gl["fingerprint"] == plan["fingerprint"]
    if any(f["kind"] in ("changed_after_issue", "deleted_from_plan") for f in flags):
        return "changed_after_issues"
    if made and not remaining:
        return "issues_created"
    if made:
        return "partly_created" if greenlit else "changed_since_greenlight"
    if greenlit:
        return "greenlit"
    return "changed_since_greenlight" if gl is not None else "found"


async def assessment(conn: asyncpg.Connection, plan: asyncpg.Record) -> Dict[str, Any]:
    client = await eskala_client(conn, plan["client_id"])
    t = await team(conn, plan["client_id"])
    fa, ce = t.get("field_associate"), t.get("content_editor")
    blocking, warnings, counts = plans.assess(
        plan["rows"] or [], plan["month"], quota(client),
        has_field_associate=fa is not None, has_content_editor=ce is not None,
        fa_has_jira=bool(fa and fa["jira_account_id"]), ce_has_jira=bool(ce and ce["jira_account_id"]),
        has_component=bool(client["jira_component_id"]))
    if plan["state"] != "found":
        blocking.insert(0, {"code": "plan_not_found", "message": plan["problem"] or "Content Plan belum ditemukan."})
    if any(f["kind"] in ("key_erased", "key_duplicated") for f in await open_flags(conn, plan["id"])):
        blocking.append({"code": "open_key_flag",
                         "message": "Ada Key yang terhapus atau ganda di Content Plan. Perbaiki di sheet-nya, lalu tandai selesai."})
    return {"blocking": blocking, "warnings": warnings, "quota": counts, "client": client, "team": t}
