"""
hub-jira-sync (spec 009 R6): field-name normalisation, paging, the cursor and `done`, the
full-changelog refetch, and the Event point comments. hub-api and Jira are replaced.
"""
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flows import hub_jira_sync as flow_mod

NAMES = {"summary": "Summary", "components": "Components", "assignee": "Assignee", "status": "Status",
         "issuetype": "Issue Type", "created": "Created", "customfield_10040": "Publication date",
         "customfield_10042": "Field Associate", "customfield_10200": "Event Type",
         "customfield_10201": "Defect Category", "customfield_10202": "Labels X", "customfield_10203": "Person"}


def _issue(key, *, histories=None, total=None, issuetype="Event", status="Judged", links=None):
    histories = histories or []
    return {
        "id": key.split("-")[1], "key": key,
        "fields": {
            "summary": f"Issue {key}",
            "issuetype": {"id": "10010", "name": issuetype},
            "status": {"id": "3", "name": status, "statusCategory": {"key": "done"}},
            "priority": {"id": "5", "name": "Lowest"},
            "created": "2026-09-01T09:00:00.000+0700",
            "updated": "2026-09-26T10:00:00.000+0700",
            "components": [{"id": "10100", "name": "Klinik Utama Gresik", "self": "u"}],
            "assignee": None,
            "customfield_10040": "2026-10-04",
            "customfield_10042": {"accountId": "acc-fa", "displayName": "Rina", "active": True},
            "customfield_10200": {"id": "1", "value": "Violation", "self": "u"},
            "customfield_10201": {"id": "2", "value": "C - Design", "child": {"id": "3", "value": "C2 - Typo"}},
            "customfield_10202": [{"id": "4", "value": "A"}, {"id": "5", "value": "B"}],
            "customfield_10203": [{"accountId": "acc-p", "displayName": "Budi"}],
            "issuelinks": links or [],
            "comment": {"comments": []},
        },
        "changelog": {"startAt": 0, "maxResults": 100, "total": total if total is not None else len(histories),
                      "histories": histories},
    }


HISTORY = {"id": "5001", "created": "2026-09-20T08:00:00.000+0700", "author": {"accountId": "acc-qa"},
           "items": [{"field": "status", "fieldId": "status", "fromString": "Review", "toString": "Judged"},
                     {"field": "Defect Category", "fieldId": "customfield_10201", "fromString": None,
                      "toString": "Parent values: C - Design(1)Level 1 values: C2 - Typo(3)"}]}


def test_normalize_issue_uses_field_names_and_simple_values():
    links = [{"type": {"name": "Relates"}, "outwardIssue": {"key": "ESKL-9"}},
             {"type": {"name": "Blocks"}, "inwardIssue": {"key": "ESKL-8"}}]
    out = flow_mod.normalize_issue(_issue("ESKL-1", histories=[HISTORY], links=links), NAMES)

    assert (out["key"], out["id"], out["type"], out["status"]) == ("ESKL-1", "1", "Event", "Judged")
    assert out["created"] == "2026-09-01T09:00:00.000+0700"
    f = out["fields"]
    assert f["Summary"] == "Issue ESKL-1"
    assert f["Components"] == [{"id": "10100", "name": "Klinik Utama Gresik"}]
    assert f["Assignee"] is None
    assert f["Status"] == "Judged" and f["Issue Type"] == "Event" and f["priority"] == "Lowest"
    assert f["Publication date"] == "2026-10-04"
    assert f["Field Associate"] == {"account_id": "acc-fa", "name": "Rina"}
    assert f["Event Type"] == "Violation"
    assert f["Defect Category"] == {"parent": "C - Design", "child": "C2 - Typo"}
    assert f["Labels X"] == ["A", "B"]
    assert f["Person"] == [{"account_id": "acc-p", "name": "Budi"}]
    assert "issuelinks" not in f and "comment" not in f
    assert out["links"] == [{"type": "Relates", "direction": "outward", "key": "ESKL-9"},
                            {"type": "Blocks", "direction": "inward", "key": "ESKL-8"}]
    assert out["changes"] == [
        {"history_id": "5001", "at": "2026-09-20T08:00:00.000+0700", "author": "acc-qa", "field": "status",
         "from": "Review", "to": "Judged"},
        {"history_id": "5001", "at": "2026-09-20T08:00:00.000+0700", "author": "acc-qa",
         "field": "Defect Category", "from": None,
         "to": "Parent values: C - Design(1)Level 1 values: C2 - Typo(3)"}]


def test_summary_components_and_assignee_are_always_present():
    out = flow_mod.normalize_fields({"issuetype": {"name": "Content"}}, {})
    assert out["Summary"] is None and out["Components"] == [] and out["Assignee"] is None


def test_jql_first_run_and_cursor_in_the_jira_users_zone():
    first = flow_mod.build_jql("2026-01-01T00:00:00+00:00", True, None)
    assert first == ('project = ESKL AND issuetype in (Content, Event) AND created >= "2026-01-01" '
                     'ORDER BY updated ASC')
    later = flow_mod.build_jql("2026-09-26T03:10:00+00:00", False, "Asia/Jakarta")
    assert 'updated >= "2026-09-26 10:10"' in later and later.endswith("ORDER BY updated ASC")
    # unknown zone: a day further back in UTC, never later
    fallback = flow_mod.build_jql("2026-09-26T03:10:00+00:00", False, None)
    assert 'updated >= "2026-09-25 03:10"' in fallback


@pytest.fixture
def env(monkeypatch):
    state = {"state": {"cursor": "2026-09-26T03:10:00+00:00", "first_run": False}, "pages": [], "hub": [],
             "searches": [], "changelog_calls": [], "comments": [], "ingest_comments": [], "fail_comment": set()}

    def fake_hub(path, params=None, timeout=600.0, json=None):
        state["hub"].append((path, json))
        if path == "jira/sync-state":
            return state["state"]
        if path == "jira/ingest":
            n = sum(1 for p, _ in state["hub"] if p == "jira/ingest")
            return {"stored": len(json["issues"]), "new_changes": 1,
                    "comments": state["ingest_comments"][n - 1] if n - 1 < len(state["ingest_comments"]) else []}
        return {}

    async def fake_fields(credentials_block_name="jira-creds"):
        return NAMES

    async def fake_tz(credentials_block_name="jira-creds"):
        return "Asia/Jakarta"

    async def fake_search(jql, next_page_token=None, max_results=50, credentials_block_name="jira-creds"):
        state["searches"].append((jql, next_page_token, max_results))
        idx = 0 if next_page_token is None else int(next_page_token)
        return state["pages"][idx]

    async def fake_changelog(issue_key, credentials_block_name="jira-creds", page_size=100):
        state["changelog_calls"].append(issue_key)
        return [HISTORY, {**HISTORY, "id": "5002", "items": [{"field": "status", "fromString": "Judged",
                                                              "toString": "Appealed"}]}]

    async def fake_comment(issue_key, text, credentials_block_name="jira-creds"):
        if issue_key in state["fail_comment"]:
            raise RuntimeError("403")
        state["comments"].append((issue_key, text))
        return {"id": "c"}

    monkeypatch.setattr(flow_mod, "get_run_logger", lambda: logging.getLogger("test"))
    monkeypatch.setattr(flow_mod, "hub_internal_call", fake_hub)
    monkeypatch.setattr(flow_mod, "jira_fields_map", fake_fields)
    monkeypatch.setattr(flow_mod, "jira_user_timezone", fake_tz)
    monkeypatch.setattr(flow_mod, "jira_search_page", fake_search)
    monkeypatch.setattr(flow_mod, "jira_issue_changelog", fake_changelog)
    monkeypatch.setattr(flow_mod, "jira_issue_comment", fake_comment)
    return state


def _ingests(state):
    return [body for path, body in state["hub"] if path == "jira/ingest"]


@pytest.mark.asyncio
async def test_pages_are_ingested_in_order_and_only_the_last_is_done(env):
    env["pages"] = [
        {"issues": [_issue("ESKL-1"), _issue("ESKL-2")], "nextPageToken": "1", "isLast": False},
        {"issues": [_issue("ESKL-3")], "nextPageToken": "2", "isLast": False},
        {"issues": [_issue("ESKL-4", issuetype="Content")], "isLast": True},
    ]

    result = await flow_mod.hub_jira_sync_flow.fn()

    assert result["error"] is None
    ingests = _ingests(env)
    assert [[i["key"] for i in b["issues"]] for b in ingests] == [["ESKL-1", "ESKL-2"], ["ESKL-3"], ["ESKL-4"]]
    assert [b["done"] for b in ingests] == [False, False, True]
    # every page carries this sync's start time; hub-api makes the next cursor from it
    assert {b["started_at"] for b in ingests} == {result["start_time"]}
    assert [s[1] for s in env["searches"]] == [None, "1", "2"]
    assert all(s[2] == 50 for s in env["searches"])
    assert 'updated >= "2026-09-26 10:10"' in env["searches"][0][0]
    assert result["summary"]["issues"] == 4 and result["summary"]["pages"] == 3


@pytest.mark.asyncio
async def test_first_run_reads_from_2026_and_skips_the_zone_lookup(env, monkeypatch):
    env["state"] = {"cursor": "2026-01-01T00:00:00+00:00", "first_run": True}
    env["pages"] = [{"issues": [], "isLast": True}]

    async def no_tz(credentials_block_name="jira-creds"):
        raise AssertionError("not needed on the first run")

    monkeypatch.setattr(flow_mod, "jira_user_timezone", no_tz)
    result = await flow_mod.hub_jira_sync_flow.fn()

    assert 'created >= "2026-01-01"' in env["searches"][0][0]
    assert _ingests(env) == [{"issues": [], "started_at": result["start_time"], "done": True}]


@pytest.mark.asyncio
async def test_a_truncated_changelog_is_read_in_full(env):
    env["pages"] = [{"issues": [_issue("ESKL-1", histories=[HISTORY], total=2), _issue("ESKL-2")], "isLast": True}]

    await flow_mod.hub_jira_sync_flow.fn()

    assert env["changelog_calls"] == ["ESKL-1"]
    issue = _ingests(env)[0]["issues"][0]
    assert [c["history_id"] for c in issue["changes"]] == ["5001", "5001", "5002"]


@pytest.mark.asyncio
async def test_point_comments_are_posted_once_then_reported(env):
    c1 = {"issue_key": "ESKL-1", "judged_at": "2026-09-20T01:00:00+00:00", "text": "Poin: -10"}
    c2 = {"issue_key": "ESKL-2", "judged_at": "2026-09-21T01:00:00+00:00", "text": "Poin: +3"}
    env["pages"] = [{"issues": [_issue("ESKL-1")], "nextPageToken": "1"},
                    {"issues": [_issue("ESKL-2")], "isLast": True}]
    env["ingest_comments"] = [[c1], [c1, c2]]   # still pending on the second page: same comment again
    env["fail_comment"] = {"ESKL-2"}

    result = await flow_mod.hub_jira_sync_flow.fn()

    assert env["comments"] == [("ESKL-1", "Poin: -10")]
    reported = [b for p, b in env["hub"] if p == "jira/comments"]
    assert reported == [{"results": [
        {"issue_key": "ESKL-1", "judged_at": c1["judged_at"], "ok": True},
        {"issue_key": "ESKL-2", "judged_at": c2["judged_at"], "ok": False, "error": "403"}]}]
    assert result["summary"]["items_failed"] == 1


@pytest.mark.asyncio
async def test_a_failure_mid_sync_posts_done_with_the_error(env, monkeypatch):
    env["pages"] = [{"issues": [_issue("ESKL-1")], "nextPageToken": "1"}]

    async def broken_search(jql, next_page_token=None, max_results=50, credentials_block_name="jira-creds"):
        if next_page_token:
            raise RuntimeError("Jira 503")
        return env["pages"][0]

    monkeypatch.setattr(flow_mod, "jira_search_page", broken_search)
    result = await flow_mod.hub_jira_sync_flow.fn()

    assert "Jira 503" in result["error"]
    ingests = _ingests(env)
    assert [b["done"] for b in ingests] == [False, True]
    assert ingests[-1]["error"] == result["error"] and ingests[-1]["issues"] == []


@pytest.mark.asyncio
async def test_validate_only_reads_and_posts_nothing(env):
    env["pages"] = [{"issues": [_issue("ESKL-1")], "isLast": True}]

    result = await flow_mod.hub_jira_sync_flow.fn(validate_only=True)

    assert [p for p, _ in env["hub"]] == ["jira/sync-state"]
    assert env["comments"] == []
    assert result["summary"]["issues"] == 1
