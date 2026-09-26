"""
hub-sanctions (spec 009 US9, research R10/R11): tick → upload each letter as a Google Doc →
report. hub-api and Drive are replaced.
"""
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flows import hub_sanctions as flow_mod

LETTERS = [{"sanction_id": "s1", "file_name": "001-SP-ESK-X-2026 - Budi", "html": "<p>SP 1</p>"},
           {"sanction_id": "s2", "file_name": "002-SP-ESK-X-2026 - Rina", "html": "<p>SP 2</p>"}]


@pytest.fixture
def env(monkeypatch):
    state = {"hub": [], "uploads": [], "tick": {"computed": 3, "issued": 2, "letters": LETTERS}, "fail": set()}

    def fake_hub(path, params=None, timeout=600.0, json=None):
        state["hub"].append((path, json))
        return state["tick"] if path == "sanctions/tick" else {}

    async def fake_upload(name, html, folder_id, credentials_block_name="google-creds"):
        if name in state["fail"]:
            raise RuntimeError("Drive 403")
        state["uploads"].append((name, html, folder_id))
        return f"doc-{len(state['uploads'])}"

    monkeypatch.setattr(flow_mod, "get_run_logger", lambda: logging.getLogger("test"))
    monkeypatch.setattr(flow_mod, "hub_internal_call", fake_hub)
    monkeypatch.setattr(flow_mod, "drive_upload_doc", fake_upload)
    monkeypatch.setenv("HUB_SP_LETTER_FOLDER_ID", "folder-sp")
    return state


def _reported(state):
    return [body for path, body in state["hub"] if path == "sanctions/letters"]


@pytest.mark.asyncio
async def test_letters_are_uploaded_and_reported(env):
    result = await flow_mod.hub_sanctions_flow.fn()

    assert result["error"] is None
    assert env["hub"][0] == ("sanctions/tick", {})
    assert env["uploads"] == [("001-SP-ESK-X-2026 - Budi", "<p>SP 1</p>", "folder-sp"),
                              ("002-SP-ESK-X-2026 - Rina", "<p>SP 2</p>", "folder-sp")]
    assert _reported(env) == [{"results": [{"sanction_id": "s1", "ok": True, "file_id": "doc-1"},
                                           {"sanction_id": "s2", "ok": True, "file_id": "doc-2"}]}]
    assert result["summary"]["computed"] == 3 and result["summary"]["issued"] == 2
    assert result["summary"]["letters_written"] == 2 and result["summary"]["items_failed"] == 0


@pytest.mark.asyncio
async def test_one_failed_upload_is_reported_and_the_rest_continue(env):
    env["fail"] = {"001-SP-ESK-X-2026 - Budi"}
    result = await flow_mod.hub_sanctions_flow.fn()

    results = _reported(env)[0]["results"]
    assert results[0] == {"sanction_id": "s1", "ok": False, "error": "Drive 403"}
    assert results[1]["ok"] is True
    assert result["summary"]["items_failed"] == 1   # drives the alert


@pytest.mark.asyncio
async def test_a_missing_folder_fails_each_letter_clearly(env, monkeypatch):
    monkeypatch.delenv("HUB_SP_LETTER_FOLDER_ID")
    result = await flow_mod.hub_sanctions_flow.fn()

    assert result["error"] is None
    assert env["uploads"] == []
    results = _reported(env)[0]["results"]
    assert [r["ok"] for r in results] == [False, False]
    assert all("HUB_SP_LETTER_FOLDER_ID" in r["error"] for r in results)
    assert "Surat SP" in result["summary"]["failures"]


@pytest.mark.asyncio
async def test_no_letters_means_no_report(env):
    env["tick"] = {"computed": 0, "issued": 0, "letters": []}
    result = await flow_mod.hub_sanctions_flow.fn()
    assert [p for p, _ in env["hub"]] == ["sanctions/tick"]
    assert result["summary"]["letters"] == 0


@pytest.mark.asyncio
async def test_validate_only_calls_nothing(env):
    result = await flow_mod.hub_sanctions_flow.fn(validate_only=True)
    assert env["hub"] == [] and env["uploads"] == []
    assert result["summary"]["folder_configured"] is True


@pytest.mark.asyncio
async def test_a_failed_tick_is_an_error_not_a_crash(env, monkeypatch):
    def broken(path, params=None, timeout=600.0, json=None):
        raise RuntimeError("hub-api 500")

    monkeypatch.setattr(flow_mod, "hub_internal_call", broken)
    result = await flow_mod.hub_sanctions_flow.fn()
    assert "hub-api 500" in result["error"]
