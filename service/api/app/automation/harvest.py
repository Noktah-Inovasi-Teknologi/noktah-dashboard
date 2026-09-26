"""
The Harvest, driven by the Registry (spec 009 US3/US4; FR-030…FR-036).

Targets are every own and competitor account of every active Eskala Client (G-7, G-39). An
account never harvested before gets 90 days, otherwise 31; the harvest-registry flow does the
collecting and appends `harvest_attempts`. Review marks (Iklan, Tidak relevan, Bukan konten
akun ini) keep a post out of performance numbers and songbird's examples (G-17);
`harvested_signals.advertisement` is kept equal to "has an active Iklan mark" so every older
reader stays right.
"""
from datetime import date
from typing import Any, Dict, List, Optional

import asyncpg

from ..errors import Invalid, NotFound

MARKS = ("iklan", "tidak_relevan", "bukan_konten_akun_ini")
MONTHLY_DAYS, FIRST_DAYS = 31, 90


_IG_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def post_url(platform: str, content_id: str, handle: Optional[str]) -> Optional[str]:
    """A post's public link, rebuilt from its id (the harvest stores no URL per post)."""
    cid = str(content_id or "").split("_")[0]
    if platform == "tiktok" and cid.isdigit() and handle:
        return f"https://www.tiktok.com/@{handle}/video/{cid}"
    if platform == "instagram" and cid.isdigit():
        n, code = int(cid), ""
        while n:
            n, r = divmod(n, 64)
            code = _IG_ALPHABET[r] + code
        return f"https://www.instagram.com/p/{code}/"
    return None


def profile_url(platform: str, handle: str) -> str:
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{handle}"
    return f"https://www.instagram.com/{handle}/"


_ACCOUNTS = """
    SELECT a.id::text AS account_id, a.platform, h.handle_text AS handle, c.id::text AS client_id,
           c.display_name AS client, CASE r.role WHEN 'owned' THEN 'own' ELSE r.role END AS role,
           NOT EXISTS (SELECT 1 FROM harvest_attempts x WHERE x.account_id = a.id
                       AND x.outcome IN ('collected', 'nothing_new'))
           AND NOT EXISTS (SELECT 1 FROM harvested_signals s WHERE s.account_id = a.id) AS first_harvest
    FROM client_account_roles r
    JOIN clients c ON c.id = r.client_id
    JOIN noktah_brands b ON b.id = c.noktah_brand_id
    JOIN accounts a ON a.id = r.account_id
    JOIN account_handles h ON h.account_id = a.id AND h.is_current
    WHERE r.is_active AND r.role IN ('owned', 'competitor') AND a.is_active
      AND b.brand_key = 'eskala' AND c.status = 'active'
      AND ($1::uuid[] IS NULL OR a.id = ANY($1::uuid[]))
    ORDER BY c.display_name, r.role DESC, h.handle_text"""


async def targets(conn: asyncpg.Connection, account_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """One entry per account (an account several Clients share is harvested once)."""
    out: Dict[str, Dict[str, Any]] = {}
    for r in await conn.fetch(_ACCOUNTS, account_ids):
        t = out.setdefault(r["account_id"], {
            "account_id": r["account_id"], "platform": r["platform"], "handle": r["handle"],
            "url": profile_url(r["platform"], r["handle"]), "clients": [], "first_harvest": r["first_harvest"],
            "window_days": FIRST_DAYS if r["first_harvest"] else MONTHLY_DAYS})
        t["clients"].append(r["client"])
    return list(out.values())


async def accounts(conn: asyncpg.Connection) -> List[Dict[str, Any]]:
    last = {r["account_id"]: r for r in await conn.fetch(
        """SELECT DISTINCT ON (account_id) account_id::text AS account_id, started_at, ended_at, outcome,
                  posts_collected, reason
           FROM harvest_attempts ORDER BY account_id, started_at DESC""")}
    out = []
    for r in await conn.fetch(_ACCOUNTS, None):
        a = last.get(r["account_id"])
        outcome = a["outcome"] if a else None
        status = ("never" if a is None else "blocked" if outcome == "blocked" else
                  "not_found" if outcome == "not_found" else "failed" if outcome == "failed" else "ok")
        out.append({"id": r["account_id"], "platform": r["platform"], "handle": r["handle"],
                    "url": profile_url(r["platform"], r["handle"]),
                    "client": {"id": r["client_id"], "name": r["client"]}, "role": r["role"],
                    "last_harvested_at": (a["ended_at"] or a["started_at"]).isoformat() if a else None,
                    "last_outcome": outcome, "last_reason": a["reason"] if a else None,
                    "posts_collected": a["posts_collected"] if a else None, "status": status,
                    "first_harvest": r["first_harvest"]})
    return out


async def account(conn: asyncpg.Connection, account_id: str) -> Dict[str, Any]:
    rows = [a for a in await accounts(conn) if a["id"] == account_id]
    if not rows:
        raise NotFound("Akun tidak ditemukan di Registry Eskala.")
    first = dict(rows[0])
    first["clients"] = [{"client": a["client"], "role": a["role"]} for a in rows]
    return first


async def posts(conn: asyncpg.Connection, account_id: str, month: date) -> List[Dict[str, Any]]:
    rows = await conn.fetch(
        """SELECT s.platform, s.content_id, s.content_type, s.published_at, s.caption, s.views, s.likes,
                  s.comments, s.shares,
                  COALESCE((SELECT array_agg(m.mark ORDER BY m.mark) FROM post_review_marks m
                            WHERE m.platform = s.platform AND m.content_id = s.content_id AND m.cleared_at IS NULL),
                           '{}') AS marks, s.profile_key
           FROM harvested_signals s
           WHERE s.account_id = $1::uuid
             AND date_trunc('month', s.published_at AT TIME ZONE 'Asia/Jakarta') = $2::date
           ORDER BY s.published_at DESC""", account_id, month)
    return [{"platform": r["platform"], "content_id": r["content_id"], "content_type": r["content_type"],
             "published_at": r["published_at"].isoformat() if r["published_at"] else None,
             "caption": r["caption"], "views": r["views"], "likes": r["likes"], "comments": r["comments"],
             "shares": r["shares"], "marks": list(r["marks"]),
             "url": post_url(r["platform"], r["content_id"], r["profile_key"])} for r in rows]


async def set_marks(conn: asyncpg.Connection, platform: str, content_id: str, marks: List[str],
                    person_id: str) -> List[str]:
    wanted = set(marks)
    bad = wanted - set(MARKS)
    if bad:
        raise Invalid(f"Tanda tidak dikenal: {', '.join(sorted(bad))}.")
    exists = await conn.fetchval(
        "SELECT 1 FROM harvested_signals WHERE platform = $1 AND content_id = $2", platform, content_id)
    if not exists:
        raise NotFound("Post tidak ditemukan.")
    current = {r["mark"] for r in await conn.fetch(
        """SELECT mark FROM post_review_marks WHERE platform = $1 AND content_id = $2 AND cleared_at IS NULL""",
        platform, content_id)}
    for m in current - wanted:
        await conn.execute(
            """UPDATE post_review_marks SET cleared_at = now(), cleared_by = $4::uuid
               WHERE platform = $1 AND content_id = $2 AND mark = $3 AND cleared_at IS NULL""",
            platform, content_id, m, person_id)
    for m in wanted - current:
        await conn.execute(
            """INSERT INTO post_review_marks (platform, content_id, mark, source, set_by)
               VALUES ($1, $2, $3, 'manual', $4::uuid)""", platform, content_id, m, person_id)
    await conn.execute(
        "UPDATE harvested_signals SET advertisement = $3 WHERE platform = $1 AND content_id = $2",
        platform, content_id, "iklan" in wanted)
    return sorted(wanted)
