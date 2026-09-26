"""
The Hub's copy of ESKL Content and Event issues, for Laporan (spec 009, research R6).

The hub-jira-sync flow reads Jira every 15 minutes and posts pages here. Issues are
upserted (their current state); changelog items are appended and never updated, keyed by
(history id, field, issue) so a page read twice changes nothing. Fields are stored by their
Jira *name*, not their id, so a field added to a Jira screen later (e.g. "Violation
Judgment") needs no code change.
"""
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import asyncpg

# The first sync reaches back to here (G-16).
HISTORY_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
# A new cursor overlaps the previous one: an issue updated while a sync ran is read again.
CURSOR_OVERLAP = timedelta(minutes=5)


def _ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


async def upsert_issues(conn: asyncpg.Connection, issues: List[Dict[str, Any]]) -> int:
    new_changes = 0
    for i in issues:
        await conn.execute(
            """INSERT INTO jira_issues (key, issue_id, issue_type, status, created_at, updated_at, fields, links, synced_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, now())
               ON CONFLICT (key) DO UPDATE SET issue_id = EXCLUDED.issue_id, issue_type = EXCLUDED.issue_type,
                   status = EXCLUDED.status, created_at = EXCLUDED.created_at, updated_at = EXCLUDED.updated_at,
                   fields = EXCLUDED.fields, links = EXCLUDED.links, synced_at = now()""",
            i["key"], str(i.get("id") or ""), i["type"], i["status"], _ts(i["created"]), _ts(i["updated"]),
            i.get("fields") or {}, i.get("links") or [])
        for c in i.get("changes") or []:
            done = await conn.fetchval(
                """INSERT INTO jira_issue_changes (issue_key, history_id, at, author_account_id, field, from_value, to_value)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)
                   ON CONFLICT (history_id, field, issue_key) DO NOTHING RETURNING 1""",
                i["key"], str(c["history_id"]), _ts(c["at"]), c.get("author"), c["field"],
                _text(c.get("from")), _text(c.get("to")))
            new_changes += done or 0
    return new_changes


def _text(v: Any) -> Optional[str]:
    if v is None:
        return None
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


async def sync_state(conn: asyncpg.Connection) -> Dict[str, Any]:
    row = await conn.fetchrow("SELECT cursor_at, last_success_at, last_error FROM jira_sync_state WHERE id = 1")
    cursor = row["cursor_at"] if row else None
    return {"cursor": (cursor or HISTORY_START).isoformat(), "first_run": cursor is None,
            "last_success_at": row["last_success_at"].isoformat() if row and row["last_success_at"] else None}


async def finish_sync(conn: asyncpg.Connection, started_at: Any, error: Optional[str] = None) -> None:
    """Move the cursor only after a complete, successful sync, so a failed sync reads again."""
    if error:
        await conn.execute("UPDATE jira_sync_state SET last_error = $1 WHERE id = 1", error)
        return
    await conn.execute(
        "UPDATE jira_sync_state SET cursor_at = $1, last_success_at = now(), last_error = NULL WHERE id = 1",
        _ts(started_at) - CURSOR_OVERLAP)


async def refreshed_at(conn: asyncpg.Connection) -> Optional[str]:
    at = await conn.fetchval("SELECT last_success_at FROM jira_sync_state WHERE id = 1")
    return at.isoformat() if at else None
