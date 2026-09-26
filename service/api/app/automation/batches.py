"""
"Buat issue Jira" batches (spec 009 US1; FR-012…FR-019).

create  → a Manager's press: every plan must be greenlit and unchanged since (FR-012/013),
          with no blocking problem; the batch is stored and hub-jira-create is started.
claim   → the flow asks what to create: per plan, only rows with no issue (FR-015), plus the
          Registry's ids (component, Field Associate, Content Editor, reporter).
result  → the flow reports rows as it goes; a created row is recorded as "this row made this
          issue" (FR-016), so a later press never re-creates it.
"""
from typing import Any, Dict, List

import asyncpg

from .. import prefect_api
from ..errors import Invalid, NotFound
from . import plans, store


async def create(conn: asyncpg.Connection, plan_ids: List[str], person_id: str) -> Dict[str, Any]:
    if not plan_ids:
        raise Invalid("Pilih minimal satu Content Plan.")
    problems, total = [], 0
    for pid in dict.fromkeys(plan_ids):
        plan = await store.load_plan(conn, pid)
        gl = await store.latest_greenlight(conn, pid)
        name = plan["client_name"]
        if gl is None:
            problems.append({"plan_id": pid, "client": name, "message": "Belum di-Greenlight."})
            continue
        if gl["fingerprint"] != plan["fingerprint"]:
            problems.append({"plan_id": pid, "client": name, "message": "Berubah setelah Greenlight. Periksa lagi, lalu beri Greenlight ulang."})
            continue
        a = await store.assessment(conn, plan)
        if a["blocking"]:
            problems.append({"plan_id": pid, "client": name, "message": a["blocking"][0]["message"]})
            continue
        remaining = store.rows_without_issue(plan["rows"] or [], await store.issue_rows(conn, pid))
        if not remaining:
            problems.append({"plan_id": pid, "client": name, "message": "Semua baris sudah punya issue."})
            continue
        total += len(remaining)
    if problems:
        raise Invalid("Sebagian Content Plan belum bisa dibuatkan issue.", plans=problems)
    batch_id = await conn.fetchval(
        """INSERT INTO jira_batches (requested_by, plan_ids, total) VALUES ($1::uuid, $2::uuid[], $3)
           RETURNING id::text""", person_id, list(dict.fromkeys(plan_ids)), total)
    return {"id": batch_id, "status": "queued", "total": total}


async def start(conn: asyncpg.Connection, batch_id: str) -> None:
    """Start the flow; a Prefect outage marks the batch failed and says so."""
    try:
        run_id = await prefect_api.run_deployment(prefect_api.JIRA_CREATE, {"batch_id": batch_id})
    except prefect_api.PrefectUnavailable:
        await conn.execute(
            "UPDATE jira_batches SET status = 'failed', error = 'Penjadwal otomasi di PC kantor tidak menjawab. Coba lagi beberapa menit lagi.', finished_at = now() WHERE id = $1::uuid",
            batch_id)
        raise
    await conn.execute("UPDATE jira_batches SET flow_run_id = $2 WHERE id = $1::uuid", batch_id, run_id)


async def claim(conn: asyncpg.Connection, batch_id: str, peek: bool = False) -> Dict[str, Any]:
    batch = await conn.fetchrow("SELECT id::text AS id, plan_ids, status FROM jira_batches WHERE id = $1::uuid", batch_id)
    if batch is None:
        raise NotFound("Batch tidak ditemukan.")
    if not peek:
        await conn.execute("UPDATE jira_batches SET status = 'running' WHERE id = $1::uuid", batch_id)
    reporter = await conn.fetchval(
        """SELECT jira_account_id FROM people WHERE jira_account_id IS NOT NULL
           AND lower(display_name) IN ('noktah', 'noktah inovasi teknologi') ORDER BY display_name LIMIT 1""")
    out = []
    for pid in batch["plan_ids"]:
        plan = await store.load_plan(conn, str(pid))
        a = await store.assessment(conn, plan)
        gl = await store.latest_greenlight(conn, str(pid))
        remaining = store.rows_without_issue(plan["rows"] or [], await store.issue_rows(conn, str(pid)))
        refused = None
        if gl is None or gl["fingerprint"] != plan["fingerprint"]:
            refused = "Content Plan berubah setelah Greenlight."
        elif a["blocking"]:
            refused = a["blocking"][0]["message"]
        out.append({
            "plan_id": str(pid), "client_id": plan["client_id"], "client_name": plan["client_name"],
            "month": plan["month"].isoformat(), "drive_file_id": plan["drive_file_id"], "tab_name": plan["tab_name"],
            "file_name": plan["file_name"], "fingerprint": plan["fingerprint"],
            "component_id": a["client"]["jira_component_id"],
            "field_associate_account": (a["team"].get("field_associate") or {}).get("jira_account_id"),
            "content_editor_account": (a["team"].get("content_editor") or {}).get("jira_account_id"),
            "field_associate_name": (a["team"].get("field_associate") or {}).get("display_name"),
            "content_editor_name": (a["team"].get("content_editor") or {}).get("display_name"),
            "reporter_account": reporter,
            "refused": refused,
            "rows_to_create": [] if refused else [{"row_number": r["row_number"], "cells": r["cells"]} for r in remaining],
        })
    return {"batch_id": batch_id, "plans": out}


async def result(conn: asyncpg.Connection, batch_id: str, rows: List[Dict[str, Any]], done: bool,
                 error: str = None) -> None:
    for r in rows:
        await conn.execute(
            """INSERT INTO jira_batch_rows (batch_id, plan_id, row_number, outcome, issue_key, reason)
               VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6)
               ON CONFLICT (batch_id, plan_id, row_number) DO UPDATE SET outcome = EXCLUDED.outcome,
                   issue_key = EXCLUDED.issue_key, reason = EXCLUDED.reason, at = now()""",
            batch_id, r["plan_id"], r["row_number"], r["outcome"], r.get("issue_key"), r.get("reason"))
        if r["outcome"] == "created" and r.get("issue_key"):
            cells = r.get("cells") or {}
            fp = r.get("fingerprint") or plans.row_fingerprint(cells)
            await conn.execute(
                """INSERT INTO content_plan_issues (issue_key, plan_id, row_number, fingerprint, cells, batch_id,
                                                   last_fingerprint, last_seen_at)
                   VALUES ($1, $2::uuid, $3, $4, $5::jsonb, $6::uuid, $4, now())
                   ON CONFLICT (issue_key) DO NOTHING""",
                r["issue_key"], r["plan_id"], r["row_number"], fp, cells, batch_id)
    await conn.execute(
        """UPDATE jira_batches b SET
               created = (SELECT count(*) FROM jira_batch_rows WHERE batch_id = b.id AND outcome = 'created'),
               failed = (SELECT count(*) FROM jira_batch_rows WHERE batch_id = b.id AND outcome <> 'created')
           WHERE id = $1::uuid""", batch_id)
    if done:
        await conn.execute(
            """UPDATE jira_batches SET status = CASE WHEN $2::text IS NULL THEN 'done' ELSE 'failed' END,
                   error = $2, finished_at = now() WHERE id = $1::uuid""", batch_id, error)


async def get(conn: asyncpg.Connection, batch_id: str) -> Dict[str, Any]:
    b = await conn.fetchrow(
        """SELECT b.id::text AS id, b.status, b.total, b.created, b.failed, b.requested_at, b.finished_at, b.error,
                  p.display_name AS requested_by
           FROM jira_batches b JOIN people p ON p.id = b.requested_by WHERE b.id = $1::uuid""", batch_id)
    if b is None:
        raise NotFound("Batch tidak ditemukan.")
    rows = await conn.fetch(
        """SELECT r.plan_id::text AS plan_id, c.display_name AS client, r.row_number, r.outcome, r.issue_key, r.reason
           FROM jira_batch_rows r JOIN content_plans p ON p.id = r.plan_id JOIN clients c ON c.id = p.client_id
           WHERE r.batch_id = $1::uuid ORDER BY c.display_name, r.row_number""", batch_id)
    return _batch(b) | {"rows": [dict(r) for r in rows]}


def _batch(b: asyncpg.Record) -> Dict[str, Any]:
    return {"id": b["id"], "status": b["status"], "total": b["total"], "created": b["created"], "failed": b["failed"],
            "requested_by": b["requested_by"], "requested_at": b["requested_at"].isoformat(),
            "finished_at": b["finished_at"].isoformat() if b["finished_at"] else None, "error": b["error"]}


async def recent(conn: asyncpg.Connection, limit: int = 20) -> List[Dict[str, Any]]:
    rows = await conn.fetch(
        """SELECT b.id::text AS id, b.status, b.total, b.created, b.failed, b.requested_at, b.finished_at, b.error,
                  p.display_name AS requested_by
           FROM jira_batches b JOIN people p ON p.id = b.requested_by ORDER BY b.requested_at DESC LIMIT $1""", limit)
    return [_batch(r) for r in rows]
