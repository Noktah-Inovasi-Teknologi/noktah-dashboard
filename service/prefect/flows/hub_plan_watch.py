"""
Hub Plan Watch: every 15 minutes, read each Eskala Client's Content Plan into the Hub
(spec 009 US1/US2, FR-010, FR-020…FR-023, research R4).

1. `automation/plan-targets` lists the plans to read: every active Eskala Client with a
   Content Plan folder, for this month and next (or one `month`, or exact `plan_ids`).
2. Per plan: find the file (`Content Plan - {client} - {month}`, exact name, one match),
   read its tab (`Sheet1` or the first) and post it to `content-plans/scan`. A plan that
   is not there, is there twice, or lacks the Tanggal/Bentuk/Topik columns is posted too,
   as `missing` / `ambiguous` / `unreadable`, so the Hub can say so.
3. hub-api's watcher answers with the Jira comments owed (a row changed after its issue
   was made): each is posted with `jira.issue.comment`, and the outcomes reported to
   `content-plans/comments`.

Plans are read 2–4 s apart (Drive/Sheets politeness).

`--validate-only`: lists the targets, reads every plan and prints what would be posted.
Posts nothing to hub-api's scan (so the watcher does not run) and nothing to Jira.

Usage (inside the container):
    docker exec prefect python flows/hub_plan_watch.py
    docker exec prefect python flows/hub_plan_watch.py --month 2026-10 --validate-only
"""
import argparse
import asyncio
import json
import os
import random
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from prefect import flow
from prefect.logging import get_run_logger

try:
    from ..tasks.content_plan_files import AmbiguousPlanFileError, missing_plan_columns, pick_plan_file
    from ..tasks.google_tasks import google_filter_files_in_folder
    from ..tasks.hub_tasks import hub_internal_call
    from ..tasks.jira_tasks import jira_issue_comment
except ImportError:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tasks.content_plan_files import AmbiguousPlanFileError, missing_plan_columns, pick_plan_file
    from tasks.google_tasks import google_filter_files_in_folder
    from tasks.hub_tasks import hub_internal_call
    from tasks.jira_tasks import jira_issue_comment

try:
    from .common.alerts import alert_hooks
    from .common.plan_sheet import read_plan_rows
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from common.alerts import alert_hooks
    from common.plan_sheet import read_plan_rows

DELAY_SECONDS = (2.0, 4.0)


async def read_target(target: Dict[str, Any], credentials_block_name: str) -> Dict[str, Any]:
    """The `content-plans/scan` body for one target: found, missing, ambiguous or unreadable."""
    body: Dict[str, Any] = {"client_id": target["client_id"], "month": target["month"]}
    files = await google_filter_files_in_folder(target["folder_id"], "Content Plan",
                                                credentials_block_name=credentials_block_name, active=True)
    try:
        found = pick_plan_file(files, target["client_name"], target["plan_label"])
    except AmbiguousPlanFileError as e:
        return {**body, "state": "ambiguous", "problem": str(e)}
    if found is None:
        return {**body, "state": "missing",
                "problem": f"Tidak ada file 'Content Plan - {target['client_name']} - {target['plan_label']}'."}
    body.update({"drive_file_id": found["id"], "file_name": found.get("name")})
    try:
        tab, header, rows = await read_plan_rows(found["id"], None, credentials_block_name)
    except Exception as e:  # noqa: BLE001 - an unreadable plan is a state, not a crash
        return {**body, "state": "unreadable", "problem": f"Content Plan tidak bisa dibaca ({str(e)[:160]}). Periksa izin berbagi file dan nama tab-nya."}
    missing = missing_plan_columns(header)
    if missing:
        return {**body, "state": "unreadable", "tab_name": tab,
                "problem": f"Tab '{tab}' tidak punya kolom {', '.join(missing)}."}
    return {**body, "state": "found", "tab_name": tab, "rows": rows}


async def post_comments(comments: List[Dict[str, Any]], jira_block: str, log) -> List[Dict[str, Any]]:
    """Post each owed comment; one result per comment, a failure never stops the rest."""
    results = []
    for c in comments:
        try:
            await jira_issue_comment(c["issue_key"], c["text"], credentials_block_name=jira_block)
            results.append({"flag_id": c["flag_id"], "ok": True})
        except Exception as e:  # noqa: BLE001
            log.warning(f"comment on {c.get('issue_key')} failed: {e}")
            results.append({"flag_id": c["flag_id"], "ok": False, "error": str(e)[:500]})
    return results


@flow(name="hub-plan-watch", description="Read Eskala Content Plans into the Hub and comment on changed issues",
      **alert_hooks("eskala"))
async def hub_plan_watch_flow(
    month: Optional[str] = None,
    plan_ids: Optional[List[str]] = None,
    validate_only: bool = False,
    credentials_block_name: str = "google-creds",
    jira_credentials_block_name: str = "jira-creds",
) -> Dict[str, Any]:
    """
    Scan Content Plans and post the watcher's Jira comments.

    Args:
        month: "YYYY-MM"; default this month and next.
        plan_ids: Exactly these plans (a fresh scan before a Greenlight or a create).
        validate_only: Read and print only; no scan is posted, no comment written.
        credentials_block_name: Google credentials block.
        jira_credentials_block_name: Jira credentials block.

    Returns:
        {start_time, end_time, data: [one entry per plan], summary, error}.
        `summary.failures` names each plan that could not be read or posted, and
        `summary.items_failed` counts comments that failed.
    """
    log = get_run_logger()
    start = datetime.now(timezone.utc)
    data: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {"targets": 0, "found": 0, "missing": 0, "ambiguous": 0, "unreadable": 0,
                               "skipped_no_folder": 0, "comments_posted": 0, "items_failed": 0,
                               "validate_only": validate_only}
    failures: Dict[str, str] = {}
    error: Optional[str] = None
    try:
        req: Dict[str, Any] = {}
        if month:
            req["month"] = month
        if plan_ids:
            req["plan_ids"] = list(plan_ids)
        targets = hub_internal_call("automation/plan-targets", json=req).get("targets") or []
        summary["targets"] = len(targets)
        for i, target in enumerate(targets):
            label = f"{target.get('client_name')} ({target.get('month')})"
            if not target.get("folder_id"):
                summary["skipped_no_folder"] += 1
                log.info(f"{label}: no Content Plan folder in the Registry; skipped")
                continue
            try:
                scan = await read_target(target, credentials_block_name)
                summary[scan["state"]] += 1
                entry = {"client": target.get("client_name"), "month": target.get("month"), "state": scan["state"],
                         "file_name": scan.get("file_name"), "rows": len(scan.get("rows") or []),
                         "problem": scan.get("problem")}
                if validate_only:
                    log.info(f"{label}: {scan['state']}, {entry['rows']} row(s)"
                             + (f" ({scan['problem']})" if scan.get("problem") else ""))
                else:
                    answer = hub_internal_call("content-plans/scan", json=scan)
                    entry["plan_id"] = answer.get("plan_id")
                    comments = answer.get("comments") or []
                    if comments:
                        results = await post_comments(comments, jira_credentials_block_name, log)
                        hub_internal_call("content-plans/comments", json={"results": results})
                        ok = sum(1 for r in results if r["ok"])
                        summary["comments_posted"] += ok
                        summary["items_failed"] += len(results) - ok
                        entry["comments"] = {"posted": ok, "failed": len(results) - ok}
                data.append(entry)
            except Exception as e:  # noqa: BLE001 - one plan never stops the rest
                failures[label] = f"{type(e).__name__}: {e}"
                log.error(f"{label}: {failures[label]}")
            if i < len(targets) - 1:
                await asyncio.sleep(random.uniform(*DELAY_SECONDS))
    except Exception as e:  # noqa: BLE001 - flows report, never raise
        error = f"{type(e).__name__}: {e}"
        log.error(error)
    if failures:
        summary["failures"] = failures
    return {"start_time": start.isoformat(), "end_time": datetime.now(timezone.utc).isoformat(),
            "data": data, "summary": summary, "error": error}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--month", default=None, help="YYYY-MM (default: this month and next)")
    parser.add_argument("--plan-ids", default=None, help="comma-separated plan ids")
    parser.add_argument("--validate-only", action="store_true", help="read and print only; post nothing")
    args = parser.parse_args()
    ids = [p.strip() for p in args.plan_ids.split(",") if p.strip()] if args.plan_ids else None
    result = asyncio.run(hub_plan_watch_flow(month=args.month, plan_ids=ids, validate_only=args.validate_only))
    print(json.dumps({"summary": result["summary"], "plans": result["data"]}, ensure_ascii=False, indent=2))
    print("error:", result["error"])
    sys.exit(1 if result["error"] else 0)
