"""Registry reads shared by several routes: the client list and one Client's record."""
from typing import Any, Dict, List, Optional

import asyncpg

from ..card.definition import completeness
from ..deps import definition

TEAM_ROLES = ["account_executive", "content_planner", "field_associate", "content_editor", "qc"]


async def current_card_values(conn: asyncpg.Connection, client_ids: List[str]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """client_id → {part → {field_key → value}} for CURRENT values only."""
    out: Dict[str, Dict[str, Dict[str, Any]]] = {cid: {"profil": {}, "guideline": {}} for cid in client_ids}
    if not client_ids:
        return out
    rows = await conn.fetch(
        """SELECT client_id::text AS cid, part, field_key, value FROM card_values
           WHERE state = 'current' AND client_id = ANY($1::uuid[])""",
        client_ids,
    )
    for r in rows:
        out[r["cid"]][r["part"]][r["field_key"]] = r["value"]
    return out


async def list_clients(conn: asyncpg.Connection, brands: List[str], include_unbranded: bool,
                       status: str) -> List[Dict[str, Any]]:
    rows = await conn.fetch(
        """SELECT c.id::text AS id, c.display_name, COALESCE(c.status,
                  CASE WHEN c.is_active THEN 'active' ELSE 'inactive' END) AS status,
                  c.is_internal, b.brand_key, b.display_name AS brand_name,
                  c.quota_post, c.quota_story, c.quota_short_video
           FROM clients c LEFT JOIN noktah_brands b ON b.id = c.noktah_brand_id
           WHERE (b.brand_key = ANY($1::text[]) OR ($2 AND c.noktah_brand_id IS NULL))
           ORDER BY c.is_internal, c.display_name""",
        brands, include_unbranded,
    )
    rows = [r for r in rows if status == "all" or r["status"] == status]
    ids = [r["id"] for r in rows]
    values = await current_card_values(conn, ids)
    team = await conn.fetch(
        """SELECT t.client_id::text AS cid, t.team_role, p.display_name
           FROM client_team_assignments t JOIN people p ON p.id = t.person_id
           WHERE t.valid_to IS NULL AND t.client_id = ANY($1::uuid[])""",
        ids,
    )
    pending = await conn.fetch(
        """SELECT client_id::text AS cid, count(*) AS n FROM card_values
           WHERE state = 'pending' AND client_id = ANY($1::uuid[]) GROUP BY 1""",
        ids,
    )
    team_by: Dict[str, Dict[str, str]] = {}
    for t in team:
        team_by.setdefault(t["cid"], {})[t["team_role"]] = t["display_name"]
    pending_by = {p["cid"]: p["n"] for p in pending}
    d = definition()
    out = []
    for r in rows:
        comp = completeness(d, values[r["id"]])
        out.append({
            "id": r["id"], "name": r["display_name"], "status": r["status"], "is_internal": r["is_internal"],
            "brand": r["brand_key"], "brand_name": r["brand_name"],
            "quotas": {"post": r["quota_post"], "story": r["quota_story"], "short_video": r["quota_short_video"]},
            "team_summary": {k: team_by.get(r["id"], {}).get(k) for k in ("account_executive", "field_associate")},
            "card_completeness": {
                "profil": f"{comp['profil']['filled']}/{comp['profil']['required']}",
                "guideline": f"{comp['guideline']['filled']}/{comp['guideline']['total']}",
            },
            "pending_approvals": pending_by.get(r["id"], 0),
        })
    return out


async def client_record(conn: asyncpg.Connection, client_id: str) -> Optional[Dict[str, Any]]:
    r = await conn.fetchrow(
        """SELECT c.id::text AS id, c.display_name, COALESCE(c.status,
                  CASE WHEN c.is_active THEN 'active' ELSE 'inactive' END) AS status,
                  c.is_internal, b.brand_key, b.display_name AS brand_name,
                  c.quota_post, c.quota_story, c.quota_short_video, c.drive_folder_id,
                  c.content_plan_folder_id, c.jira_component_id, c.version, c.card_version
           FROM clients c LEFT JOIN noktah_brands b ON b.id = c.noktah_brand_id WHERE c.id = $1::uuid""",
        client_id,
    )
    if r is None:
        return None
    team_rows = await conn.fetch(
        """SELECT t.team_role, p.id::text AS person_id, p.display_name, p.status
           FROM client_team_assignments t JOIN people p ON p.id = t.person_id
           WHERE t.client_id = $1::uuid AND t.valid_to IS NULL""",
        client_id,
    )
    accounts = await conn.fetch(
        """SELECT a.id::text AS account_id, a.platform, ah.handle_text AS handle, car.role AS relation,
                  car.is_active
           FROM client_account_roles car
           JOIN accounts a ON a.id = car.account_id
           LEFT JOIN account_handles ah ON ah.account_id = a.id AND ah.is_current
           WHERE car.client_id = $1::uuid
           ORDER BY car.role, a.platform, ah.handle_text""",
        client_id,
    )
    team = {role: None for role in TEAM_ROLES}
    for t in team_rows:
        team[t["team_role"]] = {"person_id": t["person_id"], "name": t["display_name"], "status": t["status"]}
    return {
        "id": r["id"], "name": r["display_name"], "status": r["status"], "is_internal": r["is_internal"],
        "brand": r["brand_key"], "brand_name": r["brand_name"],
        "quotas": {"post": r["quota_post"], "story": r["quota_story"], "short_video": r["quota_short_video"]},
        "drive_folder_id": r["drive_folder_id"], "content_plan_folder_id": r["content_plan_folder_id"],
        "jira_component_id": r["jira_component_id"], "team": team,
        "accounts": [dict(a) | {"relation": "own" if a["relation"] == "owned" else a["relation"]} for a in accounts],
        "version": r["version"], "card_version": r["card_version"],
    }
