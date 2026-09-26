"""
The plan watcher (spec 009 US2, research R4): what changed in a Content Plan whose issues
already exist. `inspect` is pure; `apply` stores its findings.

Per issue the Hub made from this plan:
  * its Key on exactly one row, row content changed since last seen → changed_after_issue,
    and one Jira comment listing old → new (the issue's fields are never changed);
  * its Key on no row, but a row without a Key holds the same content → key_erased;
  * its Key on no row and no such row → deleted_from_plan (the issue is never shelved);
Across rows:
  * the same Key on two rows → key_duplicated;
  * a Key the Hub didn't make → key_unknown (flagged, never acted on).
A row without a Key is simply waiting for the next "Buat issue Jira": no flag.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import asyncpg

from . import plans

COMMENT_CELL_LIMIT = 300


@dataclass
class Finding:
    kind: str
    issue_key: Optional[str]
    row_number: Optional[int]
    changes: List[Dict[str, str]] = field(default_factory=list)
    new_fingerprint: Optional[str] = None  # for changed_after_issue: the fingerprint now seen


def inspect(rows: List[Dict[str, Any]], previous_rows: List[Dict[str, Any]],
            issues: List[Dict[str, Any]]) -> List[Finding]:
    """`issues`: [{issue_key, fingerprint, last_fingerprint, cells}] the Hub made from this plan."""
    findings: List[Finding] = []
    content = plans.content_rows(rows)
    by_key: Dict[str, List[Dict[str, Any]]] = {}
    for r in content:
        k = plans.key_of(r["cells"])
        if k:
            by_key.setdefault(k, []).append(r)
    ours = {i["issue_key"]: i for i in issues}
    prev_by_key = {plans.key_of(r["cells"]): r for r in plans.content_rows(previous_rows or [])
                   if plans.key_of(r["cells"])}

    for key, found in by_key.items():
        if len(found) > 1:
            findings.append(Finding("key_duplicated", key, found[1]["row_number"]))
        elif key not in ours:
            findings.append(Finding("key_unknown", key, found[0]["row_number"]))

    unkeyed = [r for r in content if not plans.key_of(r["cells"])]
    for key, issue in ours.items():
        found = by_key.get(key, [])
        seen = issue.get("last_fingerprint") or issue["fingerprint"]
        if len(found) == 1:
            row = found[0]
            fp = plans.row_fingerprint(row["cells"])
            if fp != seen:
                before = (prev_by_key.get(key) or {}).get("cells") or issue.get("cells") or {}
                diff = plans.changes(before, row["cells"]) or plans.changes(issue.get("cells") or {}, row["cells"])
                findings.append(Finding("changed_after_issue", key, row["row_number"], diff, fp))
        elif not found:
            erased = next((r for r in unkeyed if plans.row_fingerprint(r["cells"]) in (seen, issue["fingerprint"])), None)
            if erased is not None:
                findings.append(Finding("key_erased", key, erased["row_number"]))
            else:
                findings.append(Finding("deleted_from_plan", key, None))
    return findings


def comment_text(file_name: str, row_number: int, diff: List[Dict[str, str]]) -> str:
    def cut(s: str) -> str:
        return s if len(s) <= COMMENT_CELL_LIMIT else s[:COMMENT_CELL_LIMIT] + "…"
    lines = [f"Content Plan berubah setelah issue ini dibuat ({file_name}, baris {row_number}):"]
    for d in diff:
        lines.append(f"- {d['column']}: {cut(d['old']) or '(kosong)'} → {cut(d['new']) or '(kosong)'}")
    lines.append("Isi issue ini tidak diubah otomatis. Perbarui di Jira bila perlu.")
    lines.append("— Noktah Hub")
    return "\n".join(lines)


async def apply(conn: asyncpg.Connection, plan_id: str, file_name: str, findings: List[Finding]) -> None:
    """Store findings: one open flag per (kind, key); a change to an already-flagged row
    replaces that flag's changes and queues a new comment."""
    for f in findings:
        open_flag = await conn.fetchrow(
            """SELECT id FROM content_plan_flags
               WHERE plan_id = $1::uuid AND kind = $2 AND COALESCE(issue_key, '') = COALESCE($3, '')
                 AND resolved_at IS NULL""", plan_id, f.kind, f.issue_key)
        if f.kind == "changed_after_issue":
            if open_flag:
                await conn.execute(
                    """UPDATE content_plan_flags SET changes = $2::jsonb, row_number = $3, detected_at = now(),
                              comment_state = 'pending', comment_error = NULL WHERE id = $1""",
                    open_flag["id"], f.changes, f.row_number)
            else:
                await conn.execute(
                    """INSERT INTO content_plan_flags (plan_id, kind, issue_key, row_number, changes, comment_state)
                       VALUES ($1::uuid, $2, $3, $4, $5::jsonb, 'pending')""",
                    plan_id, f.kind, f.issue_key, f.row_number, f.changes)
            await conn.execute(
                "UPDATE content_plan_issues SET last_fingerprint = $2, last_seen_at = now() WHERE issue_key = $1",
                f.issue_key, f.new_fingerprint)
        elif not open_flag:
            await conn.execute(
                """INSERT INTO content_plan_flags (plan_id, kind, issue_key, row_number)
                   VALUES ($1::uuid, $2, $3, $4)""", plan_id, f.kind, f.issue_key, f.row_number)
    await conn.execute("UPDATE content_plan_issues SET last_seen_at = now() WHERE plan_id = $1::uuid", plan_id)


async def pending_comments(conn: asyncpg.Connection, plan_id: str, file_name: str) -> List[Dict[str, Any]]:
    rows = await conn.fetch(
        """SELECT id::text AS id, issue_key, row_number, changes FROM content_plan_flags
           WHERE plan_id = $1::uuid AND kind = 'changed_after_issue' AND resolved_at IS NULL
             AND comment_state IN ('pending', 'failed')""", plan_id)
    return [{"flag_id": r["id"], "issue_key": r["issue_key"],
             "text": comment_text(file_name, r["row_number"], r["changes"] or [])} for r in rows]


async def record_comments(conn: asyncpg.Connection, results: List[Dict[str, Any]]) -> None:
    for r in results:
        if r.get("ok"):
            await conn.execute(
                """UPDATE content_plan_flags SET comment_state = 'posted', commented_at = now(), comment_error = NULL
                   WHERE id = $1::uuid""", r["flag_id"])
        else:
            await conn.execute(
                "UPDATE content_plan_flags SET comment_state = 'failed', comment_error = $2 WHERE id = $1::uuid",
                r["flag_id"], str(r.get("error") or "")[:500])
