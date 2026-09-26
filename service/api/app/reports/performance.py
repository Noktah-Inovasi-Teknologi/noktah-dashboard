"""
Laporan → Performa (spec 009 US7; FR-060…FR-062; G-17, G-33).

Per Eskala Client per month (by publication date, WIB): its own accounts beside the average
of its competitors' accounts. Posts carrying a review mark count in "how many posts" but are
left out of every average, total of reach/engagement and the top posts (G-17).

Evidence discipline (constitution VIII): every average says how many posts it rests on.
Data honesty (constitution VI): a follower count the platform didn't give is stated as
unavailable, never shown blank or guessed.
"""
from datetime import date, datetime, time
from typing import Any, Dict, List, Optional

import asyncpg

from ..automation.harvest import post_url
from .content import month_bounds

METRICS = ("views", "likes", "comments", "shares")
TOP = 5


async def _accounts(conn: asyncpg.Connection, client_id: str) -> List[Dict[str, Any]]:
    rows = await conn.fetch(
        """SELECT a.id::text AS id, a.platform, h.handle_text AS handle,
                  CASE r.role WHEN 'owned' THEN 'own' ELSE r.role END AS role
           FROM client_account_roles r JOIN accounts a ON a.id = r.account_id
           JOIN account_handles h ON h.account_id = a.id AND h.is_current
           WHERE r.client_id = $1::uuid AND r.is_active AND r.role IN ('owned', 'competitor')
           ORDER BY r.role DESC, h.handle_text""", client_id)
    return [dict(r) for r in rows]


async def _posts(conn: asyncpg.Connection, account_ids: List[str], month: date) -> List[Dict[str, Any]]:
    start, end = (datetime.combine(d, time()) for d in month_bounds(month))
    rows = await conn.fetch(
        """SELECT s.account_id::text AS account_id, s.platform, s.content_id, s.content_type, s.published_at,
                  s.caption, s.views, s.likes, s.comments, s.shares, s.profile_key,
                  EXISTS (SELECT 1 FROM post_review_marks m WHERE m.platform = s.platform
                          AND m.content_id = s.content_id AND m.cleared_at IS NULL) AS marked
           FROM harvested_signals s
           WHERE s.account_id = ANY($1::uuid[])
             AND (s.published_at AT TIME ZONE 'Asia/Jakarta') >= $2::timestamp
             AND (s.published_at AT TIME ZONE 'Asia/Jakarta') < $3::timestamp""", account_ids, start, end)
    return [dict(r) for r in rows]


async def _followers(conn: asyncpg.Connection, account_ids: List[str], month: date) -> Dict[str, int]:
    end = datetime.combine(month_bounds(month)[1], time())
    rows = await conn.fetch(
        """SELECT DISTINCT ON (account_id) account_id::text AS account_id, follower_count
           FROM account_follower_observations
           WHERE account_id = ANY($1::uuid[]) AND (observed_at AT TIME ZONE 'Asia/Jakarta') < $2::timestamp
             AND (observed_at AT TIME ZONE 'Asia/Jakarta') >= $2::timestamp - interval '62 days'
           ORDER BY account_id, observed_at DESC""", account_ids, end)
    return {r["account_id"]: r["follower_count"] for r in rows}


def _engagement(p: Dict[str, Any]) -> int:
    return (p["likes"] or 0) + (p["comments"] or 0) + (p["shares"] or 0)


def summarize(posts: List[Dict[str, Any]], followers: Optional[int], follower_note: Optional[str]) -> Dict[str, Any]:
    clean = [p for p in posts if not p["marked"]]
    out: Dict[str, Any] = {"posts": len(posts), "posts_counted": len(clean), "posts_by_type": {}}
    for p in posts:
        out["posts_by_type"][p["content_type"]] = out["posts_by_type"].get(p["content_type"], 0) + 1
    for m in METRICS:
        vals = [p[m] for p in clean if p[m] is not None]
        out[m] = {"total": sum(vals) if vals else None,
                  "average": round(sum(vals) / len(vals), 1) if vals else None, "n": len(vals)}
    with_views = [p for p in clean if p["views"]]
    out["engagement_rate_views"] = {
        "value": round(sum(_engagement(p) for p in with_views) / sum(p["views"] for p in with_views), 4)
        if with_views else None, "n": len(with_views)}
    if followers and clean:
        out["engagement_rate_followers"] = {
            "value": round(sum(_engagement(p) for p in clean) / len(clean) / followers, 4), "n": len(clean),
            "followers": followers}
    else:
        out["engagement_rate_followers"] = {"value": None, "n": len(clean),
                                            "unavailable": follower_note or "Jumlah pengikut tidak tersedia bulan ini"}
    out["top_posts"] = [_post(p) for p in sorted(clean, key=_engagement, reverse=True)[:TOP]]
    out["marked"] = [_post(p) for p in posts if p["marked"]]
    return out


def _post(p: Dict[str, Any]) -> Dict[str, Any]:
    return {"platform": p["platform"], "content_id": p["content_id"], "content_type": p["content_type"],
            "published_at": p["published_at"].isoformat() if p["published_at"] else None,
            "caption": (p["caption"] or "")[:200], "views": p["views"], "likes": p["likes"],
            "comments": p["comments"], "shares": p["shares"], "engagement": _engagement(p),
            "url": post_url(p["platform"], p["content_id"], p.get("profile_key"))}


def _change(now: Dict[str, Any], before: Dict[str, Any]) -> Dict[str, Optional[float]]:
    out = {}
    for m in METRICS:
        a, b = (now[m]["total"] or 0), (before[m]["total"] or 0)
        out[f"{m}_total"] = round((a - b) / b, 3) if b else None
    out["posts"] = round((now["posts"] - before["posts"]) / before["posts"], 3) if before["posts"] else None
    return out


def _average(summaries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The competitors' average: the mean of per-account figures (each account counts once)."""
    n = len(summaries)
    out: Dict[str, Any] = {"posts": round(sum(s["posts"] for s in summaries) / n, 1)}
    for m in METRICS:
        totals = [s[m]["total"] for s in summaries if s[m]["total"] is not None]
        avgs = [s[m]["average"] for s in summaries if s[m]["average"] is not None]
        out[m] = {"total": round(sum(totals) / len(totals), 1) if totals else None,
                  "average": round(sum(avgs) / len(avgs), 1) if avgs else None,
                  "n": sum(s[m]["n"] for s in summaries), "accounts": len(totals)}
    rates = [s["engagement_rate_views"]["value"] for s in summaries if s["engagement_rate_views"]["value"] is not None]
    out["engagement_rate_views"] = {"value": round(sum(rates) / len(rates), 4) if rates else None,
                                    "n": sum(s["engagement_rate_views"]["n"] for s in summaries)}
    fr = [s["engagement_rate_followers"]["value"] for s in summaries if s["engagement_rate_followers"]["value"] is not None]
    out["engagement_rate_followers"] = {"value": round(sum(fr) / len(fr), 4) if fr else None,
                                        "n": len(fr), "unavailable": None if fr else "Jumlah pengikut pesaing tidak tersedia"}
    return out


async def _side(conn, accounts: List[Dict[str, Any]], month: date) -> Dict[str, Any]:
    ids = [a["id"] for a in accounts]
    posts = await _posts(conn, ids, month) if ids else []
    followers = await _followers(conn, ids, month) if ids else {}
    total_followers = sum(followers.get(i, 0) for i in ids) if ids and all(i in followers for i in ids) else None
    note = None
    if ids and total_followers is None:
        missing = [a["handle"] for a in accounts if a["id"] not in followers]
        note = f"Jumlah pengikut tidak tersedia bulan ini untuk: {', '.join('@' + h for h in missing)}"
    return {"posts": posts, "followers": total_followers, "note": note}


async def client_report(conn: asyncpg.Connection, client_id: str, month: date) -> Dict[str, Any]:
    client = await conn.fetchrow(
        """SELECT c.id::text AS id, c.display_name FROM clients c JOIN noktah_brands b ON b.id = c.noktah_brand_id
           WHERE c.id = $1::uuid AND b.brand_key = 'eskala'""", client_id)
    if client is None:
        from ..errors import NotFound
        raise NotFound("Klien tidak ditemukan.")
    accounts = await _accounts(conn, client_id)
    own = [a for a in accounts if a["role"] == "own"]
    comps = [a for a in accounts if a["role"] == "competitor"]
    prev = date(month.year - (month.month == 1), (month.month - 2) % 12 + 1, 1)

    side = await _side(conn, own, month)
    own_now = summarize(side["posts"], side["followers"], side["note"])
    side_prev = await _side(conn, own, prev)
    own_prev = summarize(side_prev["posts"], side_prev["followers"], side_prev["note"])
    own_now["change"] = _change(own_now, own_prev)
    own_now["accounts"] = [{"platform": a["platform"], "handle": a["handle"]} for a in own]

    competitors = None
    if comps:
        per = []
        for a in comps:
            s = await _side(conn, [a], month)
            per.append(summarize(s["posts"], s["followers"], s["note"]))
        competitors = {"accounts": [{"platform": a["platform"], "handle": a["handle"]} for a in comps],
                       "n_accounts": len(comps), "average": _average(per)}
    return {"client": {"id": client["id"], "name": client["display_name"]}, "month": month.isoformat()[:7],
            "own": own_now, "competitors": competitors}


async def overview(conn: asyncpg.Connection, month: date) -> Dict[str, Any]:
    out = []
    for c in await conn.fetch(
            """SELECT c.id::text AS id, c.display_name FROM clients c JOIN noktah_brands b ON b.id = c.noktah_brand_id
               WHERE b.brand_key = 'eskala' AND c.status = 'active' ORDER BY c.display_name"""):
        own = [a for a in await _accounts(conn, c["id"]) if a["role"] == "own"]
        posts = await _posts(conn, [a["id"] for a in own], month) if own else []
        s = summarize(posts, None, None)
        out.append({"client": {"id": c["id"], "name": c["display_name"]}, "accounts": len(own),
                    "posts": s["posts"], "views_total": s["views"]["total"],
                    "engagement_rate_views": s["engagement_rate_views"]["value"]})
    return {"month": month.isoformat()[:7], "clients": out}
