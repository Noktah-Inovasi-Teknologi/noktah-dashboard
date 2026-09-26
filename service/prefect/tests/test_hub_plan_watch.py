"""
hub-plan-watch (spec 009 US1/US2). hub-api, Drive, Sheets and Jira are all replaced; the
real file rule (`pick_plan_file`) and column check (`missing_plan_columns`) run.
"""
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flows import hub_plan_watch as flow_mod
from flows.common import plan_sheet

SHEET = "application/vnd.google-apps.spreadsheet"


def _target(name, folder="folder-1", month="2026-10", label="Oktober 2026"):
    return {"client_id": f"id-{name}", "client_name": name, "month": month, "folder_id": folder, "plan_label": label}


@pytest.fixture
def env(monkeypatch):
    state = {"targets": [], "folders": {}, "sheets": {}, "hub": [], "comments": [], "sleeps": [],
             "scan_answer": lambda body: {"plan_id": f"plan-{body['client_id']}", "comments": []},
             "comment_fail": set()}

    def fake_hub(path, params=None, timeout=600.0, json=None):
        state["hub"].append((path, json))
        if path == "automation/plan-targets":
            return {"targets": state["targets"]}
        if path == "content-plans/scan":
            return state["scan_answer"](json)
        return {}

    async def fake_files(folder_id, file_name_pattern, credentials_block_name="google-creds", active=True, **kw):
        assert file_name_pattern == "Content Plan"
        return state["folders"].get(folder_id, [])

    async def fake_read(spreadsheet_id, tab_name=None, credentials_block_name="google-creds"):
        sheet = state["sheets"][spreadsheet_id]
        if isinstance(sheet, Exception):
            raise sheet
        return sheet

    async def fake_comment(issue_key, text, credentials_block_name="jira-creds"):
        if issue_key in state["comment_fail"]:
            raise RuntimeError("Jira 403")
        state["comments"].append((issue_key, text))
        return {"id": "1"}

    async def fake_sleep(seconds):
        state["sleeps"].append(seconds)

    monkeypatch.setattr(flow_mod, "get_run_logger", lambda: logging.getLogger("test"))
    monkeypatch.setattr(flow_mod, "hub_internal_call", fake_hub)
    monkeypatch.setattr(flow_mod, "google_filter_files_in_folder", fake_files)
    monkeypatch.setattr(flow_mod, "read_plan_rows", fake_read)
    monkeypatch.setattr(flow_mod, "jira_issue_comment", fake_comment)
    monkeypatch.setattr(flow_mod.asyncio, "sleep", fake_sleep)
    return state


def _scans(state):
    return [body for path, body in state["hub"] if path == "content-plans/scan"]


ROWS = [{"row_number": 2, "cells": {"Tanggal": "01/10/2026", "Bentuk": "Post", "Topik": "A", "Key": ""}}]


@pytest.mark.asyncio
async def test_each_plan_state_is_scanned(env):
    env["targets"] = [_target("Found"), _target("Missing", folder="f-missing"), _target("Twice", folder="f-twice"),
                      _target("NoCols", folder="f-nocols"), _target("Broken", folder="f-broken")]
    env["folders"] = {
        "folder-1": [{"id": "s1", "name": "Content Plan - Found - Oktober 2026", "mimeType": SHEET},
                     {"id": "s0", "name": "Salinan Content Plan - Found - Oktober 2026", "mimeType": SHEET}],
        "f-missing": [{"id": "x", "name": "Content Plan - Missing - September 2026", "mimeType": SHEET}],
        "f-twice": [{"id": "t1", "name": "Content Plan - Twice - Oktober 2026", "mimeType": SHEET},
                    {"id": "t2", "name": "Content Plan - Twice - Oktober 2026", "mimeType": SHEET}],
        "f-nocols": [{"id": "n1", "name": "Content Plan - NoCols - Oktober 2026", "mimeType": SHEET}],
        "f-broken": [{"id": "b1", "name": "Content Plan - Broken - Oktober 2026", "mimeType": SHEET}],
    }
    env["sheets"] = {"s1": ("Sheet1", ["Tanggal", "Bentuk", "Topik", "Key"], ROWS),
                     "n1": ("sheet3", ["Nama", "Alamat"], []),
                     "b1": RuntimeError("403 no access")}

    result = await flow_mod.hub_plan_watch_flow.fn(month="2026-10")

    assert result["error"] is None
    assert env["hub"][0] == ("automation/plan-targets", {"month": "2026-10"})
    scans = {s["client_id"]: s for s in _scans(env)}
    assert scans["id-Found"] == {"client_id": "id-Found", "month": "2026-10", "state": "found",
                                 "drive_file_id": "s1", "file_name": "Content Plan - Found - Oktober 2026",
                                 "tab_name": "Sheet1", "rows": ROWS}
    assert scans["id-Missing"]["state"] == "missing"
    assert scans["id-Twice"]["state"] == "ambiguous" and "t1" in scans["id-Twice"]["problem"]
    assert scans["id-NoCols"]["state"] == "unreadable" and "Tanggal" in scans["id-NoCols"]["problem"]
    assert scans["id-Broken"]["state"] == "unreadable" and "403" in scans["id-Broken"]["problem"]
    assert result["summary"]["found"] == 1 and result["summary"]["unreadable"] == 2
    # 2-4 s between plans, none after the last
    assert len(env["sleeps"]) == 4 and all(2.0 <= s <= 4.0 for s in env["sleeps"])


@pytest.mark.asyncio
async def test_returned_comments_are_posted_and_reported(env):
    env["targets"] = [_target("Found")]
    env["folders"] = {"folder-1": [{"id": "s1", "name": "Content Plan - Found - Oktober 2026", "mimeType": SHEET}]}
    env["sheets"] = {"s1": ("Sheet1", ["Tanggal", "Bentuk", "Topik", "Key"], ROWS)}
    env["scan_answer"] = lambda body: {"plan_id": "p1", "comments": [
        {"flag_id": "f1", "issue_key": "ESKL-1", "text": "Baris 2 berubah:\nTopik: A -> B"},
        {"flag_id": "f2", "issue_key": "ESKL-2", "text": "Baris 3 berubah"}]}
    env["comment_fail"] = {"ESKL-2"}

    result = await flow_mod.hub_plan_watch_flow.fn()

    assert env["comments"] == [("ESKL-1", "Baris 2 berubah:\nTopik: A -> B")]
    reported = [body for path, body in env["hub"] if path == "content-plans/comments"]
    assert reported == [{"results": [{"flag_id": "f1", "ok": True},
                                     {"flag_id": "f2", "ok": False, "error": "Jira 403"}]}]
    assert result["summary"]["comments_posted"] == 1
    assert result["summary"]["items_failed"] == 1   # drives the alert
    assert env["hub"][0] == ("automation/plan-targets", {})  # no month: hub-api picks this and next


@pytest.mark.asyncio
async def test_no_comment_round_trip_when_none_owed(env):
    env["targets"] = [_target("Found")]
    env["folders"] = {"folder-1": [{"id": "s1", "name": "Content Plan - Found - Oktober 2026", "mimeType": SHEET}]}
    env["sheets"] = {"s1": ("Sheet1", ["Tanggal", "Bentuk", "Topik", "Key"], ROWS)}

    await flow_mod.hub_plan_watch_flow.fn(plan_ids=["p1"])

    assert env["hub"][0] == ("automation/plan-targets", {"plan_ids": ["p1"]})
    assert [p for p, _ in env["hub"]] == ["automation/plan-targets", "content-plans/scan"]
    assert env["comments"] == []


@pytest.mark.asyncio
async def test_validate_only_reads_but_posts_nothing(env):
    env["targets"] = [_target("Found"), _target("NoFolder", folder=None)]
    env["folders"] = {"folder-1": [{"id": "s1", "name": "Content Plan - Found - Oktober 2026", "mimeType": SHEET}]}
    env["sheets"] = {"s1": ("Sheet1", ["Tanggal", "Bentuk", "Topik", "Key"], ROWS)}

    result = await flow_mod.hub_plan_watch_flow.fn(validate_only=True)

    assert [p for p, _ in env["hub"]] == ["automation/plan-targets"]
    assert env["comments"] == []
    assert result["data"][0]["state"] == "found" and result["data"][0]["rows"] == 1
    assert result["summary"]["skipped_no_folder"] == 1


@pytest.mark.asyncio
async def test_one_failing_plan_does_not_stop_the_rest(env):
    env["targets"] = [_target("A", folder="fa"), _target("B", folder="fb")]
    env["folders"] = {"fb": [{"id": "sb", "name": "Content Plan - B - Oktober 2026", "mimeType": SHEET}]}
    env["sheets"] = {"sb": ("Sheet1", ["Tanggal", "Bentuk", "Topik"], ROWS)}

    def answer(body):
        if body["client_id"] == "id-A":
            raise RuntimeError("hub-api 500")
        return {"plan_id": "pb", "comments": []}

    env["scan_answer"] = answer
    result = await flow_mod.hub_plan_watch_flow.fn()

    assert result["error"] is None
    assert "A (2026-10)" in result["summary"]["failures"]
    assert result["summary"]["found"] == 1


@pytest.mark.asyncio
async def test_read_plan_rows_uses_sheet1_or_the_first_tab(monkeypatch):
    calls = []

    async def info(spreadsheet_id, credentials_block_name="google-creds"):
        return {"sheets": [{"title": "sheet3"}, {"title": "Arsip"}]}

    async def raw(spreadsheet_id, sheet_name, credentials_block_name="google-creds", **kw):
        calls.append(sheet_name)
        return {"values": [["Tanggal", "Topik", "Key"], ["01/10/2026", "A", '=HYPERLINK("u","ESKL-1")']]}

    monkeypatch.setattr(plan_sheet, "google_read_spreadsheet_info", info)
    monkeypatch.setattr(plan_sheet, "google_read_sheet_raw", raw)
    tab, header, rows = await plan_sheet.read_plan_rows("s1")
    assert tab == "sheet3" and calls == ["'sheet3'"]
    assert header == ["Tanggal", "Topik", "Key"] and rows[0]["row_number"] == 2

    tab, _, _ = await plan_sheet.read_plan_rows("s1", "Sheet1")
    assert tab == "Sheet1" and calls[-1] == "'Sheet1'"
