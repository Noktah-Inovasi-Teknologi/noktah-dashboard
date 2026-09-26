"""
hub-jira-create (spec 009 US1, research R3/R4). Every outside call is replaced: hub-api,
the plan sheet, Jira bulk create, Jira entity properties and the Key-cell write. The real
converter runs (its `.fn`, outside Prefect), with the Hashmaps sheet made unreadable to
prove the Hub path never touches it.
"""
import logging
import os
import sys
from collections.abc import Mapping

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flows import hub_jira_create as flow_mod
from tasks import content_plan_rows as rows_mod
from tasks import utility_tasks

JIRA = "https://noktah.atlassian.net"
HEADER = ["No.", "Tanggal", "Bentuk", "Topik", "Key", "TicketID"]


class _Forbidden(Mapping):
    """A hashmap that fails the test if anything reads it."""

    def __getitem__(self, key):
        raise AssertionError("the Hub path read the Hashmaps sheet")

    def get(self, key, default=None):
        raise AssertionError("the Hub path read the Hashmaps sheet")

    def __iter__(self):
        raise AssertionError("the Hub path read the Hashmaps sheet")

    def __len__(self):
        raise AssertionError("the Hub path read the Hashmaps sheet")


def _rows(n, start=2, topic="Topik"):
    return [{"row_number": start + i, "cells": {"No.": str(i + 1), "Tanggal": f"{i + 1:02d}/10/2026",
                                                "Bentuk": "Post", "Topik": f"{topic} {i + 1}", "Key": "",
                                                "TicketID": ""}}
            for i in range(n)]


def _plan(rows, fingerprint=None, **over):
    plan = {"plan_id": "plan-1", "client_id": "client-1", "client_name": "Klinik Utama Gresik",
            "drive_file_id": "sheet-1", "tab_name": "Sheet1",
            "fingerprint": fingerprint if fingerprint is not None else rows_mod.plan_fingerprint(rows),
            "component_id": "10100", "field_associate_account": "acc-fa", "content_editor_account": "acc-ce",
            "reporter_account": "acc-noktah", "refused": None,
            "rows_to_create": [{"row_number": r["row_number"], "cells": dict(r["cells"])} for r in rows]}
    plan.update(over)
    return plan


@pytest.fixture
def env(monkeypatch):
    state = {"claim": {"batch_id": "batch-1", "plans": []}, "sheets": {}, "hub": [], "bulk": [], "props": {},
             "writes": [], "bulk_errors": {}, "next_key": 100, "prop_reads": []}

    def fake_hub(path, params=None, timeout=600.0, json=None):
        state["hub"].append((path, json))
        if path == "jira-batches/claim":
            return state["claim"]
        return {}

    async def fake_read(spreadsheet_id, tab_name=None, credentials_block_name="google-creds"):
        header, rows = state["sheets"][spreadsheet_id]
        return tab_name or "Sheet1", header, [{"row_number": r["row_number"], "cells": dict(r["cells"])} for r in rows]

    async def fake_bulk(issue_updates, credentials_block_name="jira-creds", max_issues=45):
        assert len(issue_updates) <= 45
        state["bulk"].append(issue_updates)
        created, errors = [], []
        for i, issue in enumerate(issue_updates):
            if i in state["bulk_errors"]:
                errors.append({"status": 400, "failedElementNumber": i,
                               "elementErrors": {"errors": {"summary": state["bulk_errors"][i]}}})
                continue
            key = f"ESKL-{state['next_key']}"
            state["next_key"] += 1
            state["props"][key] = issue["properties"][0]["value"]
            created.append({"id": key[5:], "key": key})
        # Jira does not promise an order: hand the keys back reversed.
        return {"status": "success", "created_issues": list(reversed(created)), "errors": errors}

    async def fake_get_property(issue_key, property_key, credentials_block_name="jira-creds"):
        assert property_key == "noktah.plan-row"
        state["prop_reads"].append(issue_key)
        return state["props"][issue_key]

    async def fake_write(spreadsheet_id, cells, credentials_block_name="google-creds"):
        state["writes"].append((spreadsheet_id, dict(cells)))
        return {}

    monkeypatch.setattr(flow_mod, "get_run_logger", lambda: logging.getLogger("test"))
    monkeypatch.setattr(flow_mod, "hub_internal_call", fake_hub)
    monkeypatch.setattr(flow_mod, "read_plan_rows", fake_read)
    monkeypatch.setattr(flow_mod, "create_issues_bulk", fake_bulk)
    monkeypatch.setattr(flow_mod, "jira_issue_get_property", fake_get_property)
    monkeypatch.setattr(flow_mod, "sheets_write_cells", fake_write)
    monkeypatch.setattr(flow_mod, "convert_content_plan_row_to_jira_issue",
                        utility_tasks.convert_content_plan_row_to_jira_issue.fn)
    monkeypatch.setattr(utility_tasks, "get_run_logger", lambda: logging.getLogger("test"))
    for name in ("COMPONENTS", "WORKERS", "FIELD_ASSOCIATE", "CONTENT_EDITOR"):
        monkeypatch.setattr(utility_tasks, name, _Forbidden())
    monkeypatch.setenv("JIRA_URL", JIRA)
    return state


def _results(state):
    return [row for path, body in state["hub"] if path == "jira-batches/result" for row in body["rows"]]


async def _run(**kw):
    return await flow_mod.hub_jira_create_flow.fn(batch_id="batch-1", **kw)


@pytest.mark.asyncio
async def test_keys_are_mapped_by_property_not_response_order(env):
    rows = _rows(3)
    env["sheets"]["sheet-1"] = (HEADER, rows)
    env["claim"]["plans"] = [_plan(rows)]

    result = await _run()

    assert result["error"] is None
    # payload i got ESKL-(100+i), whatever order Jira returned them in
    by_row = {r["row_number"]: r for r in _results(env)}
    assert {n: r["issue_key"] for n, r in by_row.items()} == {2: "ESKL-100", 3: "ESKL-101", 4: "ESKL-102"}
    assert all(r["outcome"] == "created" for r in by_row.values())
    assert by_row[3]["fingerprint"] == rows_mod.row_fingerprint(rows[1]["cells"])
    assert by_row[3]["cells"]["Topik"] == "Topik 2"
    assert sorted(env["prop_reads"]) == ["ESKL-100", "ESKL-101", "ESKL-102"]
    assert "failures" not in result["summary"]
    # progress after the plan, then the final done
    assert [b["done"] for p, b in env["hub"] if p == "jira-batches/result"] == [False, True]


@pytest.mark.asyncio
async def test_only_the_key_column_is_written(env):
    rows = _rows(2)
    env["sheets"]["sheet-1"] = (HEADER, rows)
    env["claim"]["plans"] = [_plan(rows)]

    await _run()

    assert len(env["writes"]) == 1
    spreadsheet_id, cells = env["writes"][0]
    assert spreadsheet_id == "sheet-1"
    # Key is column E; TicketID (F) is never touched
    assert cells == {
        "'Sheet1'!E2": f'=HYPERLINK("{JIRA}/browse/ESKL-100","ESKL-100")',
        "'Sheet1'!E3": f'=HYPERLINK("{JIRA}/browse/ESKL-101","ESKL-101")',
    }


@pytest.mark.asyncio
async def test_payload_uses_registry_ids_carries_property_and_no_metadata(env):
    rows = _rows(1)
    env["sheets"]["sheet-1"] = (HEADER, rows)
    env["claim"]["plans"] = [_plan(rows)]

    await _run()

    issue = env["bulk"][0][0]
    assert "metadata" not in issue
    assert issue["properties"] == [{"key": "noktah.plan-row",
                                    "value": {"plan_id": "plan-1", "row_number": 2, "batch_id": "batch-1"}}]
    fields = issue["fields"]
    assert fields["components"] == [{"id": "10100"}]
    assert fields["customfield_10042"] == {"accountId": "acc-fa"}
    assert fields["assignee"] == {"accountId": "acc-fa"}
    assert fields["customfield_10043"] == {"accountId": "acc-ce"}
    assert fields["reporter"] == {"accountId": "acc-noktah"}
    assert fields["summary"] == "Topik 1"


@pytest.mark.asyncio
async def test_a_plan_changed_since_its_greenlight_is_refused(env):
    rows = _rows(2)
    greenlit = rows_mod.plan_fingerprint(rows)
    edited = _rows(2)
    edited[1]["cells"]["Topik"] = "Diganti setelah Greenlight"
    env["sheets"]["sheet-1"] = (HEADER, edited)
    env["claim"]["plans"] = [_plan(rows, fingerprint=greenlit)]

    result = await _run()

    assert env["bulk"] == [] and env["writes"] == []
    reported = _results(env)
    assert [(r["row_number"], r["outcome"], r["reason"]) for r in reported] == [
        (2, "refused", flow_mod.CHANGED), (3, "refused", flow_mod.CHANGED)]
    assert "Klinik Utama Gresik" in result["summary"]["failures"]


@pytest.mark.asyncio
async def test_a_plan_refused_by_hub_api_reports_its_rows_without_creating(env):
    rows = _rows(3)
    rows[0]["cells"]["Key"] = "ESKL-7"  # already has an issue: not a row to refuse
    env["sheets"]["sheet-1"] = (HEADER, rows)
    env["claim"]["plans"] = [_plan([], refused="Plan berubah setelah Greenlight.", rows_to_create=[])]

    result = await _run()

    assert env["bulk"] == []
    assert [(r["row_number"], r["outcome"]) for r in _results(env)] == [(3, "refused"), (4, "refused")]
    assert result["summary"]["refused"] == 2


@pytest.mark.asyncio
async def test_failed_elements_are_reported_against_their_rows(env):
    rows = _rows(3)
    env["sheets"]["sheet-1"] = (HEADER, rows)
    env["claim"]["plans"] = [_plan(rows)]
    env["bulk_errors"] = {1: "Field 'summary' is invalid"}

    result = await _run()

    by_row = {r["row_number"]: r for r in _results(env)}
    assert by_row[3]["outcome"] == "failed"
    assert "summary" in by_row[3]["reason"]
    assert by_row[2]["outcome"] == "created" and by_row[4]["outcome"] == "created"
    assert {by_row[2]["issue_key"], by_row[4]["issue_key"]} == {"ESKL-100", "ESKL-101"}
    assert result["summary"]["failures"]["Klinik Utama Gresik"].startswith("1 baris tidak dibuat")
    # the failed row gets no Key cell
    assert set(env["writes"][0][1]) == {"'Sheet1'!E2", "'Sheet1'!E4"}


@pytest.mark.asyncio
async def test_a_row_that_already_carries_a_key_is_never_recreated(env):
    rows = _rows(2)
    claimed = _plan(rows)
    fresh = _rows(2)
    fresh[0]["cells"]["Key"] = "ESKL-55"  # typed in after the scan; Key is outside the fingerprint
    env["sheets"]["sheet-1"] = (HEADER, fresh)
    env["claim"]["plans"] = [claimed]

    await _run()

    by_row = {r["row_number"]: r for r in _results(env)}
    assert by_row[2]["outcome"] == "refused" and "ESKL-55" in by_row[2]["reason"]
    assert by_row[3]["outcome"] == "created"
    assert len(env["bulk"][0]) == 1


@pytest.mark.asyncio
async def test_bulk_create_is_chunked_at_45(env):
    rows = _rows(50)
    env["sheets"]["sheet-1"] = (HEADER, rows)
    env["claim"]["plans"] = [_plan(rows)]

    result = await _run()

    assert [len(c) for c in env["bulk"]] == [45, 5]
    assert result["summary"]["created"] == 50
    keys = {r["row_number"]: r["issue_key"] for r in _results(env)}
    assert keys[2] == "ESKL-100" and keys[51] == "ESKL-149"


@pytest.mark.asyncio
async def test_validate_only_creates_writes_and_reports_nothing(env):
    rows = _rows(2)
    env["sheets"]["sheet-1"] = (HEADER, rows)
    env["claim"]["plans"] = [_plan(rows)]

    result = await _run(validate_only=True)

    assert env["bulk"] == [] and env["writes"] == []
    assert [p for p, _ in env["hub"]] == ["jira-batches/claim"]
    assert result["summary"]["would_create"] == 2
    assert all("payload" in r for r in result["data"])


@pytest.mark.asyncio
async def test_a_broken_claim_reports_done_with_the_error(env, monkeypatch):
    def broken(path, params=None, timeout=600.0, json=None):
        env["hub"].append((path, json))
        if path == "jira-batches/claim":
            raise RuntimeError("hub-api down")
        return {}

    monkeypatch.setattr(flow_mod, "hub_internal_call", broken)
    result = await _run()

    assert "hub-api down" in result["error"]
    final = [b for p, b in env["hub"] if p == "jira-batches/result"]
    assert final == [{"batch_id": "batch-1", "rows": [], "done": True, "error": result["error"]}]
