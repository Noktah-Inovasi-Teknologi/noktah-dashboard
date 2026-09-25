"""
Tests for content-plan tab resolution (_read_plan_sheet).

A content plan's tab is *usually* `Sheet1`, and the flow used to require it.
Measured 2026-09-03: Klinik Mata Bireuen's September plan had one tab named
`sheet3`, so the read returned `400 Unable to parse range: Sheet1` and that
client produced no Jira issues for the month at all.

The fallback must be narrow. Reading "some other tab" after any failure would
mask a permissions error or an outage as a naming problem, so only a genuine
name mismatch may be recovered from.

No network: the two Google tasks are replaced on the flow module.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flows import content_plan_spreadsheet_to_jira_issue as cp

SHEET_ID = "1cJOHYKOGzGxFBYpnLNWOfYOKOvrlDnXCR8vBhHQj5MM"


class ParseRangeError(Exception):
    """Stands in for googleapiclient's HttpError 400 'Unable to parse range'."""


def install(monkeypatch, *, tabs, readable, info_error=None):
    """Fake the two Google tasks; record what was asked for."""
    asked = []

    async def fake_read(spreadsheet_id, sheet_name, credentials_block_name=None, header_row=0):
        asked.append(sheet_name)
        if sheet_name not in readable:
            raise ParseRangeError(f"Unable to parse range: {sheet_name}")
        return {"data": readable[sheet_name], "dataframe_info": {}}

    async def fake_info(spreadsheet_id, credentials_block_name=None):
        if info_error:
            raise info_error
        return {"title": "Content Plan", "sheets": [{"title": t} for t in tabs]}

    monkeypatch.setattr(cp, "google_read_sheet_data", fake_read)
    monkeypatch.setattr(cp, "google_read_spreadsheet_info", fake_info)
    return asked


@pytest.mark.asyncio
async def test_the_common_case_costs_exactly_one_request(monkeypatch):
    rows = [{"Topik": "Edukasi"}]
    asked = install(monkeypatch, tabs=["Sheet1"], readable={"Sheet1": rows})

    data, tab = await cp._read_plan_sheet(SHEET_ID)

    assert (data["data"], tab) == (rows, "Sheet1")
    assert asked == ["Sheet1"], "a plan on Sheet1 must not trigger a metadata lookup"


@pytest.mark.asyncio
async def test_a_differently_named_tab_is_read_instead(monkeypatch):
    # The real Klinik Mata Bireuen case.
    rows = [{"Topik": "Katarak"}]
    asked = install(monkeypatch, tabs=["sheet3"], readable={"sheet3": rows})

    data, tab = await cp._read_plan_sheet(SHEET_ID)

    assert (data["data"], tab) == (rows, "sheet3")
    assert asked == ["Sheet1", "sheet3"]


@pytest.mark.asyncio
async def test_the_first_tab_wins_when_there_are_several(monkeypatch):
    install(monkeypatch, tabs=["Plan Sept", "Arsip"], readable={"Plan Sept": [], "Arsip": []})

    _, tab = await cp._read_plan_sheet(SHEET_ID)

    assert tab == "Plan Sept"


@pytest.mark.asyncio
async def test_a_failure_that_is_not_about_the_name_is_re_raised(monkeypatch):
    """
    `Sheet1` is present, so the read failed for some other reason. Falling back
    to another tab here would turn a permissions error or an outage into a
    silent, wrong-looking success.
    """
    install(monkeypatch, tabs=["Sheet1", "Arsip"], readable={"Arsip": []})

    with pytest.raises(ParseRangeError):
        await cp._read_plan_sheet(SHEET_ID)


@pytest.mark.asyncio
async def test_the_original_error_survives_a_failed_metadata_lookup(monkeypatch):
    # The read error is what actually went wrong; the lookup error is noise.
    install(
        monkeypatch,
        tabs=["sheet3"],
        readable={},
        info_error=RuntimeError("metadata lookup exploded"),
    )

    with pytest.raises(ParseRangeError):
        await cp._read_plan_sheet(SHEET_ID)


@pytest.mark.asyncio
async def test_a_spreadsheet_reporting_no_tabs_re_raises(monkeypatch):
    install(monkeypatch, tabs=[], readable={})

    with pytest.raises(ParseRangeError):
        await cp._read_plan_sheet(SHEET_ID)
