"""
Content issues as Laporan reads them from the Jira copy (spec 009 US5/US6): each issue with
its Client (by Jira component), publication date, Field Associate and Content Editor, and its
status history, including the Defect Category and Return Reason set on a Return (G-35).
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import asyncpg

from . import stations

F_PUBLICATION = "Publication date"
F_FA = "Field Associate"
F_CE = "Content Editor"
F_COMPONENTS = "Components"
F_DEFECT = "Defect Category"
F_RETURN_REASON = "Return Reason"

_CODE = re.compile(r"\b([A-Z])(\d{0,2})\s+-\s")


def parse_defect(text: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """(code, letter) from a Defect Category as stored: a {parent, child} dict, or the
    changelog's "Parent values: C - …(10064)Level 1 values: C2 - …(10066)" string."""
    if not text:
        return None, None
    if isinstance(text, dict):
        text = f"{text.get('parent') or ''} {text.get('child') or ''}"
    found = _CODE.findall(str(text) + " ")
    if not found:
        return None, None
    letter = found[0][0]
    code = next((f"{l}{n}" for l, n in found if n), letter)
    return code, letter


@dataclass
class Transition:
    at: datetime
    from_status: str
    to_status: str
    author: Optional[str]
    defect: Optional[str] = None
    defect_code: Optional[str] = None
    origin: Optional[str] = None       # the defect's letter (the origin station), if recorded
    reason: Optional[str] = None


@dataclass
class Content:
    key: str
    summary: str
    status: str
    created_at: datetime
    publication: Optional[date]
    client_id: Optional[str]
    client_name: Optional[str]
    fa_account: Optional[str]
    ce_account: Optional[str]
    transitions: List[Transition] = field(default_factory=list)

    def intervals(self, now: datetime) -> List[Tuple[str, datetime, datetime]]:
        """(status, start, end) for each stretch, the last one open until now."""
        first = self.transitions[0].from_status if self.transitions else self.status
        out, status, start = [], first, self.created_at
        for t in self.transitions:
            out.append((status, start, t.at))
            status, start = t.to_status, t.at
        out.append((status, start, now))
        return out

    def published_at(self) -> Optional[datetime]:
        for t in self.transitions:
            if stations.is_published(t.to_status):
                return t.at
        return None


def _date(v: Any) -> Optional[date]:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _account(v: Any) -> Optional[str]:
    if isinstance(v, dict):
        return v.get("account_id")
    return v or None


def month_bounds(month: date) -> Tuple[date, date]:
    end = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    return month, end


async def load(conn: asyncpg.Connection, month: date) -> List[Content]:
    """Content issues whose publication date falls in `month` (the month a content belongs to)."""
    start, end = month_bounds(month)
    clients = {r["jira_component_id"]: r for r in await conn.fetch(
        """SELECT c.id::text AS id, c.display_name, c.jira_component_id FROM clients c
           JOIN noktah_brands b ON b.id = c.noktah_brand_id
           WHERE b.brand_key = 'eskala' AND c.jira_component_id IS NOT NULL""")}
    rows = await conn.fetch(
        """SELECT key, status, created_at, fields FROM jira_issues
           WHERE issue_type = 'Content'
             AND (fields->>'Publication date') >= $1 AND (fields->>'Publication date') < $2""",
        start.isoformat(), end.isoformat())
    keys = [r["key"] for r in rows]
    changes = await conn.fetch(
        """SELECT issue_key, history_id, at, author_account_id, field, from_value, to_value
           FROM jira_issue_changes WHERE issue_key = ANY($1::text[])
             AND field IN ('status', $2, $3) ORDER BY at, id""", keys, F_DEFECT, F_RETURN_REASON)
    by_history: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for c in changes:
        by_history.setdefault((c["issue_key"], c["history_id"]), {})[c["field"]] = c
    out = []
    for r in rows:
        fields = r["fields"] or {}
        comps = fields.get(F_COMPONENTS) or []
        client = next((clients[str(c.get("id"))] for c in comps if isinstance(c, dict) and str(c.get("id")) in clients), None)
        transitions = []
        for (k, _h), group in by_history.items():
            if k != r["key"] or "status" not in group:
                continue
            s = group["status"]
            d = group.get(F_DEFECT)
            code, letter = parse_defect(d["to_value"]) if d else (None, None)
            reason = group.get(F_RETURN_REASON)
            transitions.append(Transition(at=s["at"], from_status=s["from_value"] or "", to_status=s["to_value"] or "",
                                          author=s["author_account_id"], defect=d["to_value"] if d else None,
                                          defect_code=code, origin=letter if letter in stations.STATIONS else None,
                                          reason=reason["to_value"] if reason else None))
        transitions.sort(key=lambda t: t.at)
        out.append(Content(key=r["key"], summary=str(fields.get("Summary") or ""), status=r["status"],
                           created_at=r["created_at"], publication=_date(fields.get(F_PUBLICATION)),
                           client_id=client["id"] if client else None,
                           client_name=client["display_name"] if client else None,
                           fa_account=_account(fields.get(F_FA)), ce_account=_account(fields.get(F_CE)),
                           transitions=transitions))
    return out


def hours(a: datetime, b: datetime) -> float:
    return max((b - a).total_seconds(), 0.0) / 3600.0


def end_of_day_wib(d: date) -> datetime:
    from ..workdays import end_of
    return end_of(d)
