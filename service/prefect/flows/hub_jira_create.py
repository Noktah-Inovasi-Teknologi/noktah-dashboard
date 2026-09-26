"""
Hub Jira Create: "Buat issue Jira" for greenlit Content Plans (spec 009 US1, FR-012…FR-019).

hub-api stores the batch a Manager asked for and starts this flow with its `batch_id`. The
flow does the outside writes (Jira issues, the plan's Key cells) and reports every row back:

1. `jira-batches/claim` returns, per plan, the rows still without an issue and the
   Registry's Jira ids (component, Field Associate, Content Editor, reporter).
2. Per plan:
   - a plan hub-api already refused (changed since its Greenlight, or blocked) -> every row
     `refused` with hub-api's reason;
   - re-read the sheet and recompute the plan fingerprint exactly as hub-api does
     (tasks/content_plan_rows.py). Different -> every row `refused`
     ("Plan berubah setelah Greenlight."). A row that now carries a Key is refused too;
   - convert each row with the Registry's ids, add the `noktah.plan-row` entity property,
     drop `metadata`, and create in bulk (≤45 per call);
   - read each created key's `noktah.plan-row` property to learn its row. Never the
     response order: bulk create does not promise one (research R3);
   - write `=HYPERLINK(url, key)` into each created row's **Key** cell (TicketID is never
     touched);
   - report the plan's rows to `jira-batches/result`.
3. A final `result` with `done: true` (and `error` when the run broke).

`--validate-only`: claims the batch (hub-api then shows it as running; the claim is the
only way to learn its rows), builds and validates every payload, and prints them. It
creates no issue, writes no cell and reports no result.

Usage (inside the container):
    docker exec prefect python flows/hub_jira_create.py --batch-id <uuid>
    docker exec prefect python flows/hub_jira_create.py --batch-id <uuid> --validate-only
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from prefect import flow
from prefect.logging import get_run_logger

try:
    from ..tasks import jira_adf as adf
    from ..tasks.content_plan_rows import (
        content_rows, key_cell_a1, key_hyperlink, key_of, plan_fingerprint, row_fingerprint,
    )
    from ..tasks.google_tasks import sheets_write_cells
    from ..tasks.hub_tasks import hub_internal_call
    from ..tasks.jira_tasks import create_issues_bulk, jira_issue_get_property
    from ..tasks.utility_tasks import PLAN_ROW_PROPERTY, convert_content_plan_row_to_jira_issue, hub_issue_payload
    from ..blocks.jira_credentials import JiraCredentials
except ImportError:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tasks import jira_adf as adf
    from tasks.content_plan_rows import (
        content_rows, key_cell_a1, key_hyperlink, key_of, plan_fingerprint, row_fingerprint,
    )
    from tasks.google_tasks import sheets_write_cells
    from tasks.hub_tasks import hub_internal_call
    from tasks.jira_tasks import create_issues_bulk, jira_issue_get_property
    from tasks.utility_tasks import PLAN_ROW_PROPERTY, convert_content_plan_row_to_jira_issue, hub_issue_payload
    from blocks.jira_credentials import JiraCredentials

try:
    from .common.alerts import alert_hooks
    from .common.plan_sheet import read_plan_rows
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from common.alerts import alert_hooks
    from common.plan_sheet import read_plan_rows

# Constitution II: bulk creation in batches of at most 45.
BULK_CHUNK = 45
CHANGED = "Plan berubah setelah Greenlight."


def _row(plan_id: str, row_number: int, outcome: str, cells: Optional[Dict[str, Any]] = None,
         issue_key: Optional[str] = None, reason: Optional[str] = None) -> Dict[str, Any]:
    """One `jira-batches/result` row."""
    cells = cells or {}
    out: Dict[str, Any] = {"plan_id": plan_id, "row_number": row_number, "outcome": outcome,
                           "fingerprint": row_fingerprint(cells), "cells": cells}
    if issue_key:
        out["issue_key"] = issue_key
    if reason:
        out["reason"] = reason
    return out


def _jira_error_text(error: Dict[str, Any]) -> str:
    """Jira's reason for one failed element of a bulk create, in one line."""
    element = error.get("elementErrors") or {}
    parts = [f"{k}: {v}" for k, v in (element.get("errors") or {}).items()]
    parts += [str(m) for m in (element.get("errorMessages") or [])]
    return "Jira menolak: " + ("; ".join(parts) if parts else json.dumps(error, ensure_ascii=False)[:300])


def _chunks(items: List[Any], size: int) -> List[List[Any]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


async def _jira_url(jira_block: str) -> str:
    url = os.environ.get("JIRA_URL", "").strip()
    if not url:
        url = (await JiraCredentials.load_or_env(jira_block)).jira_url or ""
    return url.rstrip("/")


async def _refused_rows(plan: Dict[str, Any], reason: str, google_block: str, log) -> List[Dict[str, Any]]:
    """Every row of a plan hub-api refused. hub-api sends no rows for a refused plan, so the
    rows still lacking a Key are read from the sheet; if it cannot be read, none are listed."""
    rows = plan.get("rows_to_create") or []
    if not rows and plan.get("drive_file_id"):
        try:
            _, _, fresh = await read_plan_rows(plan["drive_file_id"], plan.get("tab_name"), google_block)
            rows = [r for r in content_rows(fresh) if not key_of(r["cells"])]
        except Exception as e:  # noqa: BLE001 - the refusal stands either way
            log.warning(f"{plan.get('client_name')}: could not list the refused plan's rows: {e}")
    return [_row(plan["plan_id"], r["row_number"], "refused", r.get("cells"), reason=reason) for r in rows]


async def _process_plan(plan: Dict[str, Any], batch_id: str, validate_only: bool, jira_url: str,
                        google_block: str, jira_block: str, log) -> Tuple[List[Dict[str, Any]], List[str]]:
    """(result rows, problems) for one claimed plan. Never raises once an issue may exist."""
    plan_id, client = plan["plan_id"], plan.get("client_name") or plan["plan_id"]
    problems: List[str] = []

    if plan.get("refused"):
        log.warning(f"{client}: refused by hub-api: {plan['refused']}")
        return await _refused_rows(plan, plan["refused"], google_block, log), problems

    claimed = plan.get("rows_to_create") or []
    if not claimed:
        return [], problems

    # Re-read right before creating: the Greenlight covered the plan as it was then.
    try:
        tab, header, fresh = await read_plan_rows(plan["drive_file_id"], plan.get("tab_name"), google_block)
    except Exception as e:  # noqa: BLE001
        reason = f"Content Plan tidak bisa dibaca ({str(e)[:160]}). Periksa izin berbagi file dan nama tab-nya."
        return [_row(plan_id, r["row_number"], "refused", r.get("cells"), reason=reason) for r in claimed], problems
    if plan_fingerprint(fresh) != plan.get("fingerprint"):
        log.warning(f"{client}: plan changed since its Greenlight; refusing {len(claimed)} row(s)")
        return [_row(plan_id, r["row_number"], "refused", r.get("cells"), reason=CHANGED) for r in claimed], problems

    fresh_cells = {r["row_number"]: r["cells"] for r in fresh}
    results: Dict[int, Dict[str, Any]] = {}
    to_send: List[Tuple[int, Dict[str, Any], Dict[str, Any]]] = []
    for r in claimed:
        n = r["row_number"]
        cells = fresh_cells.get(n, r.get("cells") or {})
        existing = key_of(cells)
        if existing:
            results[n] = _row(plan_id, n, "refused", cells, reason=f"Baris sudah punya Key {existing}.")
            continue
        try:
            issue = convert_content_plan_row_to_jira_issue(
                row=cells, client_name=client,
                component_id=plan.get("component_id"),
                field_associate_account=plan.get("field_associate_account"),
                content_editor_account=plan.get("content_editor_account"),
                reporter_account=plan.get("reporter_account"),
                registry_only=True,
            )
            payload = hub_issue_payload(adf.sanitize_issue(issue), plan_id, n, batch_id)
        except Exception as e:  # noqa: BLE001 - one bad row never stops the plan
            results[n] = _row(plan_id, n, "failed", cells, reason=f"Baris tidak bisa diubah jadi issue: {e}")
            continue
        fatal = adf.find_fatal_problems(payload)
        if fatal:
            results[n] = _row(plan_id, n, "failed", cells, reason="Isi baris ditolak: " + "; ".join(fatal))
            continue
        to_send.append((n, cells, payload))

    if validate_only:
        for n, cells, payload in to_send:
            results[n] = {**_row(plan_id, n, "would_create", cells), "payload": payload}
        return [results[k] for k in sorted(results)], problems

    created: Dict[int, str] = {}
    for chunk in _chunks(to_send, BULK_CHUNK):
        submitted = {n: cells for n, cells, _ in chunk}
        resp = await create_issues_bulk([p for _, _, p in chunk], credentials_block_name=jira_block,
                                        max_issues=BULK_CHUNK)
        created_issues = resp.get("created_issues") or []
        if resp.get("status") != "success":
            if created_issues:  # never expected on a non-201, but do not lose a created key
                problems.append(f"bulk create returned an error with {len(created_issues)} issue(s) created")
            for n, cells in submitted.items():
                results[n] = _row(plan_id, n, "failed", cells, reason=f"Jira gagal: {resp.get('error')}")
            if not created_issues:
                continue
        for err in resp.get("errors") or []:
            idx = err.get("failedElementNumber")
            if isinstance(idx, int) and 0 <= idx < len(chunk):
                n = chunk[idx][0]
                results[n] = _row(plan_id, n, "failed", submitted[n], reason=_jira_error_text(err))

        # Map every created key to its row by the property it carries, never by order.
        unmapped: List[str] = []
        for issue in created_issues:
            key = issue.get("key")
            try:
                value = await jira_issue_get_property(key, PLAN_ROW_PROPERTY, credentials_block_name=jira_block)
            except Exception as e:  # noqa: BLE001
                log.error(f"{key}: could not read {PLAN_ROW_PROPERTY}: {e}")
                unmapped.append(key)
                continue
            n = value.get("row_number") if isinstance(value, dict) else None
            if (not isinstance(value, dict) or value.get("plan_id") != plan_id
                    or value.get("batch_id") != batch_id or n not in submitted or n in created):
                log.error(f"{key}: {PLAN_ROW_PROPERTY} = {value!r} matches no row sent for this plan")
                unmapped.append(key)
                continue
            created[n] = key
            results[n] = _row(plan_id, n, "created", submitted[n], issue_key=key)
        if unmapped:
            problems.append(f"issue {', '.join(unmapped)} dibuat tapi barisnya tidak bisa dipastikan")
        for n, cells in submitted.items():
            if n not in results:
                results[n] = _row(plan_id, n, "failed", cells,
                                  reason="Issue mungkin sudah dibuat, tapi barisnya tidak bisa dipastikan"
                                         + (f" (cek {', '.join(unmapped)})" if unmapped else "") + ".")

    # The Key cells: the only cells this flow ever writes.
    if created:
        try:
            cells = {key_cell_a1(tab, header, n): key_hyperlink(jira_url, key) for n, key in created.items()}
            await sheets_write_cells(plan["drive_file_id"], cells, credentials_block_name=google_block)
        except Exception as e:  # noqa: BLE001 - the issues exist; say the Key is missing
            note = f"Issue dibuat, tapi Key belum ditulis ke plan: {e}"
            problems.append(note)
            for n in created:
                results[n]["reason"] = note

    return [results[k] for k in sorted(results)], problems


@flow(name="hub-jira-create", description="Create Jira issues for greenlit Content Plans (Buat issue Jira)",
      **alert_hooks("eskala"))
async def hub_jira_create_flow(
    batch_id: str,
    validate_only: bool = False,
    credentials_block_name: str = "google-creds",
    jira_credentials_block_name: str = "jira-creds",
) -> Dict[str, Any]:
    """
    Create the issues of one Hub batch and report every row.

    Args:
        batch_id: The `jira_batches` id hub-api started this run with.
        validate_only: Claim, build and validate only; no issue, cell or result is written.
        credentials_block_name: Google credentials block (plan sheets).
        jira_credentials_block_name: Jira credentials block.

    Returns:
        {start_time, end_time, data: [result rows], summary, error}. `summary.failures`
        ({client: what went wrong}) is set when a row failed or was refused, which is what
        the #eskala-otomasi alert reads.
    """
    log = get_run_logger()
    start = datetime.now(timezone.utc)
    data: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {"batch_id": batch_id, "validate_only": validate_only, "plans": 0,
                               "created": 0, "failed": 0, "refused": 0, "would_create": 0}
    failures: Dict[str, str] = {}
    error: Optional[str] = None
    try:
        # validate-only peeks: the batch is not marked running
        claim = hub_internal_call("jira-batches/claim", json={"batch_id": batch_id, "peek": validate_only})
        jira_url = await _jira_url(jira_credentials_block_name)
        for plan in claim.get("plans") or []:
            summary["plans"] += 1
            client = plan.get("client_name") or plan.get("plan_id")
            try:
                rows, problems = await _process_plan(plan, batch_id, validate_only, jira_url,
                                                     credentials_block_name, jira_credentials_block_name, log)
            except Exception as e:  # noqa: BLE001 - only reachable before any issue exists
                rows, problems = [], [f"{type(e).__name__}: {e}"]
                log.error(f"{client}: {problems[0]}")
            data.extend(rows)
            for r in rows:
                summary[r["outcome"]] = summary.get(r["outcome"], 0) + 1
            bad = [r for r in rows if r["outcome"] in ("failed", "refused")]
            if bad or problems:
                first = bad[0].get("reason") if bad else problems[0]
                failures[client] = (f"{len(bad)} baris tidak dibuat. {first}" if bad else first)
            log.info(f"{client}: " + ", ".join(f"{o} {sum(1 for r in rows if r['outcome'] == o)}"
                                               for o in sorted({r['outcome'] for r in rows})) if rows
                     else f"{client}: nothing to create")
            if not validate_only:
                hub_internal_call("jira-batches/result",
                                  json={"batch_id": batch_id, "rows": rows, "done": False})
        if not validate_only:
            hub_internal_call("jira-batches/result", json={"batch_id": batch_id, "rows": [], "done": True})
    except Exception as e:  # noqa: BLE001 - flows report, never raise
        error = f"{type(e).__name__}: {e}"
        log.error(error)
        if not validate_only:
            try:
                hub_internal_call("jira-batches/result",
                                  json={"batch_id": batch_id, "rows": [], "done": True, "error": error})
            except Exception as e2:  # noqa: BLE001
                log.error(f"could not report the failure to hub-api: {e2}")
    if failures:
        summary["failures"] = failures
    return {"start_time": start.isoformat(), "end_time": datetime.now(timezone.utc).isoformat(),
            "data": data, "summary": summary, "error": error}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch-id", required=True, help="jira_batches id (from the Hub)")
    parser.add_argument("--validate-only", action="store_true",
                        help="claim, build and validate only; create nothing, write nothing, report nothing")
    args = parser.parse_args()
    result = asyncio.run(hub_jira_create_flow(batch_id=args.batch_id, validate_only=args.validate_only))
    print(json.dumps({"summary": result["summary"], "rows": result["data"]}, ensure_ascii=False, indent=2,
                     default=str))
    print("error:", result["error"])
    sys.exit(1 if result["error"] else 0)
