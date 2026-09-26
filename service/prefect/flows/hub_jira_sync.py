"""
Hub Jira Sync: every 15 minutes, copy ESKL Content and Event issues into the Hub for Laporan
and the incentive points (spec 009, FR-050, research R6).

1. `jira/sync-state` gives the cursor (and whether this is the first sync).
2. `GET /field` maps field ids to names: the Hub stores fields by NAME, so a field added to
   a Jira screen later (e.g. "Violation Judgment") needs no code change.
3. `project = ESKL AND issuetype in (Content, Event) AND updated >= <cursor>` (the first
   sync: `created >= "2026-01-01"`), pages of 50 with the changelog; an issue whose
   embedded changelog is truncated has its full history read from `/issue/{key}/changelog`.
4. Each page is normalised and posted to `jira/ingest`; the last page says `done: true`
   with this sync's start time, which hub-api turns into the next cursor (minus 5 minutes).
   A failure posts `done: true` with the error, so the cursor does not move.
5. The Event point comments hub-api returns are posted to Jira, then reported to
   `jira/comments`.

`--validate-only`: reads and counts; posts nothing to hub-api or Jira.

Usage (inside the container):
    docker exec prefect python flows/hub_jira_sync.py
    docker exec prefect python flows/hub_jira_sync.py --validate-only
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from prefect import flow
from prefect.logging import get_run_logger

try:
    from ..tasks.hub_tasks import hub_internal_call
    from ..tasks.jira_tasks import (
        jira_fields_map, jira_issue_changelog, jira_issue_comment, jira_search_page, jira_user_timezone,
    )
except ImportError:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from tasks.hub_tasks import hub_internal_call
    from tasks.jira_tasks import (
        jira_fields_map, jira_issue_changelog, jira_issue_comment, jira_search_page, jira_user_timezone,
    )

try:
    from .common.alerts import alert_hooks
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from common.alerts import alert_hooks

PROJECT = "ESKL"
ISSUE_TYPES = ("Content", "Event")
HISTORY_START = "2026-01-01"
PAGE_SIZE = 50

# Always present in `fields`, by these names, whatever /field calls them.
ALWAYS = {"summary": "Summary", "components": "Components", "assignee": "Assignee"}
# System fields reduced to their name.
NAMED = ("issuetype", "status", "priority", "resolution", "project")
# Carried at the top level of the issue, or not useful in `fields`.
SKIP = ("issuelinks", "comment", "worklog", "attachment", "watches", "votes", "timetracking",
        "subtasks", "lastViewed", "aggregateprogress", "progress")


# --------------------------------------------------------------------------------------------
# Normalisation (pure)
# --------------------------------------------------------------------------------------------

def normalize_value(value: Any) -> Any:
    """A Jira field value in the shape hub-api reads (app/incentive/points.py, reports/content.py).

    user → {"account_id", "name"}; option → its value; option list → list of values;
    cascading option → {"parent", "child"}; anything else as is (dates stay strings,
    rich text stays ADF). Components and the named system fields (status, priority, …)
    are handled by `normalize_fields`, which knows the field id.
    """
    if isinstance(value, list):
        return [normalize_value(v) for v in value]
    if not isinstance(value, dict):
        return value
    if "accountId" in value:
        return {"account_id": value.get("accountId"), "name": value.get("displayName")}
    if "value" in value:
        if "child" in value:
            child = value.get("child") or {}
            return {"parent": value.get("value"), "child": child.get("value") if isinstance(child, dict) else child}
        return value.get("value")
    return value


def normalize_fields(raw: Dict[str, Any], names: Dict[str, str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for fid, value in (raw or {}).items():
        if fid in SKIP:
            continue
        name = ALWAYS.get(fid) or names.get(fid) or fid
        if fid == "components":
            out[name] = [{"id": c.get("id"), "name": c.get("name")} for c in (value or []) if isinstance(c, dict)]
        elif fid in NAMED:
            out[name] = value.get("name") if isinstance(value, dict) else value
        else:
            out[name] = normalize_value(value)
    for fid, name in ALWAYS.items():
        out.setdefault(name, [] if fid == "components" else None)
    return out


def normalize_links(raw_links: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    links = []
    for link in raw_links or []:
        kind = (link.get("type") or {}).get("name")
        if link.get("outwardIssue"):
            links.append({"type": kind, "direction": "outward", "key": link["outwardIssue"].get("key")})
        elif link.get("inwardIssue"):
            links.append({"type": kind, "direction": "inward", "key": link["inwardIssue"].get("key")})
    return links


def normalize_changes(histories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    changes = []
    for h in histories or []:
        author = (h.get("author") or {}).get("accountId")
        for item in h.get("items") or []:
            changes.append({"history_id": str(h.get("id")), "at": h.get("created"), "author": author,
                            "field": item.get("field"), "from": item.get("fromString"), "to": item.get("toString")})
    return changes


def normalize_issue(issue: Dict[str, Any], names: Dict[str, str],
                    histories: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    raw = issue.get("fields") or {}
    if histories is None:
        histories = (issue.get("changelog") or {}).get("histories") or []
    return {
        "key": issue["key"],
        "id": str(issue.get("id") or ""),
        "type": (raw.get("issuetype") or {}).get("name"),
        "status": (raw.get("status") or {}).get("name"),
        "created": raw.get("created"),
        "updated": raw.get("updated"),
        "fields": normalize_fields(raw, names),
        "links": normalize_links(raw.get("issuelinks") or []),
        "changes": normalize_changes(histories),
    }


def changelog_truncated(issue: Dict[str, Any]) -> bool:
    log = issue.get("changelog") or {}
    total = log.get("total")
    return isinstance(total, int) and total > len(log.get("histories") or [])


def build_jql(cursor: Optional[str], first_run: bool, tz_name: Optional[str]) -> str:
    """The search. JQL reads a bare date-time in the API user's time zone, so the cursor is
    converted to it; with no known zone, a day earlier in UTC (reading more is harmless,
    reading less loses changes)."""
    head = f"project = {PROJECT} AND issuetype in ({', '.join(ISSUE_TYPES)})"
    if first_run or not cursor:
        return f'{head} AND created >= "{HISTORY_START}" ORDER BY updated ASC'
    at = datetime.fromisoformat(str(cursor).replace("Z", "+00:00"))
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    try:
        local = at.astimezone(ZoneInfo(tz_name)) if tz_name else at.astimezone(timezone.utc) - timedelta(days=1)
    except Exception:  # noqa: BLE001 - an unknown zone name
        local = at.astimezone(timezone.utc) - timedelta(days=1)
    return f'{head} AND updated >= "{local.strftime("%Y-%m-%d %H:%M")}" ORDER BY updated ASC'


# --------------------------------------------------------------------------------------------
# Flow
# --------------------------------------------------------------------------------------------

async def post_comments(comments: List[Dict[str, Any]], jira_block: str, log) -> List[Dict[str, Any]]:
    results = []
    for c in comments:
        try:
            await jira_issue_comment(c["issue_key"], c["text"], credentials_block_name=jira_block)
            results.append({"issue_key": c["issue_key"], "judged_at": c["judged_at"], "ok": True})
        except Exception as e:  # noqa: BLE001
            log.warning(f"point comment on {c.get('issue_key')} failed: {e}")
            results.append({"issue_key": c["issue_key"], "judged_at": c["judged_at"], "ok": False,
                            "error": str(e)[:500]})
    return results


@flow(name="hub-jira-sync", description="Copy ESKL Content and Event issues into the Hub",
      **alert_hooks("eskala"))
async def hub_jira_sync_flow(validate_only: bool = False,
                             jira_credentials_block_name: str = "jira-creds") -> Dict[str, Any]:
    """
    One sync of the Hub's Jira copy.

    Args:
        validate_only: Read and count only; nothing is posted to hub-api or Jira.
        jira_credentials_block_name: Jira credentials block.

    Returns:
        {start_time, end_time, data: [], summary, error}. `summary.items_failed` counts
        point comments that could not be posted.
    """
    log = get_run_logger()
    start = datetime.now(timezone.utc)
    summary: Dict[str, Any] = {"validate_only": validate_only, "pages": 0, "issues": 0, "changes": 0,
                               "histories_refetched": 0, "new_changes": 0, "comments_posted": 0,
                               "items_failed": 0}
    error: Optional[str] = None
    comments: Dict[tuple, Dict[str, Any]] = {}
    done_posted = False
    try:
        state = hub_internal_call("jira/sync-state", json={})
        names = await jira_fields_map(credentials_block_name=jira_credentials_block_name)
        tz_name = None
        if not state.get("first_run"):
            try:
                tz_name = await jira_user_timezone(credentials_block_name=jira_credentials_block_name)
            except Exception as e:  # noqa: BLE001 - fall back to a wider window
                log.warning(f"Jira user time zone unknown ({e}); reading a day further back")
        jql = build_jql(state.get("cursor"), bool(state.get("first_run")), tz_name)
        summary["jql"] = jql
        log.info(jql)

        token: Optional[str] = None
        while True:
            page = await jira_search_page(jql, next_page_token=token, max_results=PAGE_SIZE,
                                          credentials_block_name=jira_credentials_block_name)
            summary["pages"] += 1
            issues = []
            for raw in page.get("issues") or []:
                histories = None
                if changelog_truncated(raw):
                    histories = await jira_issue_changelog(raw["key"], credentials_block_name=jira_credentials_block_name)
                    summary["histories_refetched"] += 1
                issues.append(normalize_issue(raw, names, histories))
            summary["issues"] += len(issues)
            summary["changes"] += sum(len(i["changes"]) for i in issues)
            token = page.get("nextPageToken")
            last = bool(page.get("isLast")) or not token
            if not validate_only:
                answer = hub_internal_call("jira/ingest", json={"issues": issues, "started_at": start.isoformat(),
                                                               "done": last})
                done_posted = last
                summary["new_changes"] += answer.get("new_changes") or 0
                for c in answer.get("comments") or []:
                    comments[(c["issue_key"], c["judged_at"])] = c
            if last:
                break

        if comments and not validate_only:
            results = await post_comments(list(comments.values()), jira_credentials_block_name, log)
            hub_internal_call("jira/comments", json={"results": results})
            summary["comments_posted"] = sum(1 for r in results if r["ok"])
            summary["items_failed"] = len(results) - summary["comments_posted"]
        log.info(f"synced {summary['issues']} issue(s) over {summary['pages']} page(s)")
    except Exception as e:  # noqa: BLE001 - flows report, never raise
        error = f"{type(e).__name__}: {e}"
        log.error(error)
        if not validate_only and not done_posted:
            try:
                hub_internal_call("jira/ingest", json={"issues": [], "started_at": start.isoformat(),
                                                       "done": True, "error": error})
            except Exception as e2:  # noqa: BLE001
                log.error(f"could not report the failure to hub-api: {e2}")
    return {"start_time": start.isoformat(), "end_time": datetime.now(timezone.utc).isoformat(),
            "data": [], "summary": summary, "error": error}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--validate-only", action="store_true", help="read and count only; post nothing")
    args = parser.parse_args()
    result = asyncio.run(hub_jira_sync_flow(validate_only=args.validate_only))
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print("error:", result["error"])
    sys.exit(1 if result["error"] else 0)
