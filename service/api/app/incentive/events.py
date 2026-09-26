"""
Judged Events as the Hub reads them from its Jira copy: each Event's Outcome (points.py),
the Person it concerns, and the one comment per judgement the Hub posts back (G-45).
"""
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional

import asyncpg

from . import points


@dataclass
class Event:
    key: str
    summary: str
    status: str
    created_at: datetime
    judged_at: Optional[datetime]
    account_id: Optional[str]
    person_id: Optional[str]
    person_name: Optional[str]
    outcome: points.Outcome
    links: List[str]

    @property
    def month(self) -> date:
        """An Event belongs to the month it was reported in (v2.1 §6.7: ticket time)."""
        return self.created_at.date().replace(day=1)


async def load(conn: asyncpg.Connection, keys: Optional[Iterable[str]] = None) -> List[Event]:
    keys = list(keys) if keys is not None else None
    rows = await conn.fetch(
        """SELECT i.key, i.status, i.created_at, i.fields, i.links,
                  (SELECT max(c.at) FROM jira_issue_changes c
                   WHERE c.issue_key = i.key AND c.field = 'status' AND c.to_value = 'Judged') AS judged_at
           FROM jira_issues i
           WHERE i.issue_type = 'Event' AND ($1::text[] IS NULL OR i.key = ANY($1::text[]))
           ORDER BY i.created_at""", keys)
    people = {r["jira_account_id"]: r for r in await conn.fetch(
        """SELECT id::text AS id, display_name, jira_account_id, started_on FROM people
           WHERE jira_account_id IS NOT NULL""")}
    out = []
    for r in rows:
        fields: Dict[str, Any] = r["fields"] or {}
        account = points.person_account(fields)
        person = people.get(account) if account else None
        judged_at = r["judged_at"]
        if judged_at is None and r["status"] == "Judged":
            judged_at = r["created_at"]  # judged before the Hub's history reached back
        outcome = points.compute(fields, status=r["status"], judged_at=judged_at, created_at=r["created_at"],
                                 started_on=person["started_on"] if person else None,
                                 person_known=person is not None)
        out.append(Event(key=r["key"], summary=str(fields.get("Summary") or fields.get("summary") or ""),
                         status=r["status"], created_at=r["created_at"], judged_at=judged_at,
                         account_id=account, person_id=person["id"] if person else None,
                         person_name=(person["display_name"] if person else
                                      (fields.get("Person") or {}).get("name") if isinstance(fields.get("Person"), dict) else None),
                         outcome=outcome, links=[l.get("key") for l in (r["links"] or []) if isinstance(l, dict)]))
    return out


async def queue_comments(conn: asyncpg.Connection, keys: Iterable[str]) -> List[Dict[str, Any]]:
    """Queue one comment per (Event, judgement) for the Events just synced, and return every
    comment still to post (pending, or failed last time)."""
    for e in await load(conn, keys):
        # No comment for incomplete or unjudged Events, nor for ones judged before v2.1 counts:
        # a note on every old ticket would be noise (they never count toward anything).
        if e.judged_at is None or e.outcome.state in ("belum_lengkap", "not_judged", "on_hold", "reference"):
            continue
        await conn.execute(
            """INSERT INTO event_point_comments (issue_key, judged_at, points, text)
               VALUES ($1, $2, $3, $4) ON CONFLICT (issue_key, judged_at) DO NOTHING""",
            e.key, e.judged_at, e.outcome.points, points.comment_text(e.outcome))
    rows = await conn.fetch(
        """SELECT issue_key, judged_at, text FROM event_point_comments
           WHERE state IN ('pending', 'failed') ORDER BY judged_at LIMIT 100""")
    return [{"issue_key": r["issue_key"], "judged_at": r["judged_at"].isoformat(), "text": r["text"]} for r in rows]


async def record_comments(conn: asyncpg.Connection, results: List[Dict[str, Any]]) -> None:
    for r in results:
        judged_at = datetime.fromisoformat(str(r["judged_at"]).replace("Z", "+00:00"))
        if r.get("ok"):
            await conn.execute(
                """UPDATE event_point_comments SET state = 'posted', posted_at = now(), error = NULL
                   WHERE issue_key = $1 AND judged_at = $2""", r["issue_key"], judged_at)
        else:
            await conn.execute(
                """UPDATE event_point_comments SET state = 'failed', error = $3
                   WHERE issue_key = $1 AND judged_at = $2""", r["issue_key"], judged_at, str(r.get("error") or "")[:500])
