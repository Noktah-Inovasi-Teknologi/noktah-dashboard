"""
harvest-registry (spec 009 US3, research R12): the window each account gets, the 30-minute
spacing, the attempt outcome, and validate-only. hub-api, the harvest engine, the database
and asyncio.sleep are replaced.
"""
import asyncio
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("HARVEST_DB_URL", "postgresql://test:test@localhost/test")

from flows import harvest_registry as flow_mod


def _target(handle, first=False, window=None):
    t = {"account_id": f"acc-{handle}", "platform": "instagram", "handle": handle,
         "url": f"https://www.instagram.com/{handle}/", "clients": ["Klinik Utama Gresik"], "first_harvest": first}
    if window is not None:
        t["window_days"] = window
    return t


def _result(collected=0, blocked=0, not_found=0, unresolved=0, failed=0, error=None):
    return {"summary": {"items_collected": collected, "profiles_blocked": blocked, "profiles_not_found": not_found,
                        "profiles_unresolved": unresolved, "items_failed": failed}, "error": error}


@pytest.fixture
def env(monkeypatch):
    state = {"targets": [], "hub": [], "harvests": [], "attempts": [], "sleeps": [], "results": {},
             "selector_days": []}

    def fake_hub(path, params=None, timeout=600.0, json=None):
        state["hub"].append((path, json))
        return {"targets": state["targets"]}

    def fake_selector(days):
        state["selector_days"].append(days)
        return f"window-{days}"

    async def fake_run_harvest(profiles, depth_selector, harvest_name=None, list_depth=30, **kw):
        state["harvests"].append({"profiles": profiles, "selector": depth_selector, "harvest_name": harvest_name,
                                  "list_depth": list_depth})
        return state["results"].get(profiles[0], _result(collected=2))

    async def fake_attempt(**kwargs):
        state["attempts"].append(kwargs)
        return len(state["attempts"])

    async def fake_sleep(seconds):
        state["sleeps"].append(seconds)

    monkeypatch.setattr(flow_mod, "get_run_logger", lambda: logging.getLogger("test"))
    monkeypatch.setattr(flow_mod, "hub_internal_call", fake_hub)
    monkeypatch.setattr(flow_mod, "make_time_window_selector", fake_selector)
    monkeypatch.setattr(flow_mod, "run_harvest", fake_run_harvest)
    monkeypatch.setattr(flow_mod, "harvest_attempt_record", fake_attempt)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return state


@pytest.mark.asyncio
async def test_first_harvest_gets_90_days_and_the_rest_31(env):
    env["targets"] = [_target("new", first=True), _target("old")]

    result = await flow_mod.harvest_registry_flow.fn()

    assert result["error"] is None
    assert env["selector_days"] == [90, 31]
    assert [h["profiles"] for h in env["harvests"]] == [["https://www.instagram.com/new/"],
                                                        ["https://www.instagram.com/old/"]]
    assert all(h["list_depth"] == 150 and h["harvest_name"] is None for h in env["harvests"])
    assert [a["window_days"] for a in env["attempts"]] == [90, 31]
    assert env["hub"] == [("harvest/targets", {})]


@pytest.mark.asyncio
async def test_hub_api_window_wins_when_given(env):
    env["targets"] = [_target("x", first=False, window=90)]
    await flow_mod.harvest_registry_flow.fn()
    assert env["selector_days"] == [90]


@pytest.mark.asyncio
async def test_30_minutes_between_accounts_never_after_the_last(env):
    env["targets"] = [_target("a"), _target("b"), _target("c")]
    await flow_mod.harvest_registry_flow.fn()
    assert env["sleeps"] == [1800, 1800]


@pytest.mark.asyncio
async def test_one_account_waits_for_nothing(env):
    env["targets"] = [_target("only")]
    result = await flow_mod.harvest_registry_flow.fn(account_ids=["acc-only"], trigger="manual")
    assert env["sleeps"] == []
    assert env["hub"] == [("harvest/targets", {"account_ids": ["acc-only"]})]
    assert env["attempts"][0]["trigger"] == "manual"
    assert result["summary"]["collected"] == 1


@pytest.mark.asyncio
async def test_each_outcome_is_recorded(env):
    env["targets"] = [_target(h) for h in ("got", "none", "blocked", "gone", "ghost", "broke")]
    env["results"] = {
        "https://www.instagram.com/got/": _result(collected=3, failed=1),
        "https://www.instagram.com/none/": _result(),
        "https://www.instagram.com/blocked/": _result(collected=1, blocked=1),
        "https://www.instagram.com/gone/": _result(not_found=1),
        "https://www.instagram.com/ghost/": _result(unresolved=1),
        "https://www.instagram.com/broke/": _result(error="Drive quota"),
    }

    result = await flow_mod.harvest_registry_flow.fn()

    outcomes = [(a["account_id"], a["outcome"], a["posts_collected"]) for a in env["attempts"]]
    assert outcomes == [("acc-got", "collected", 3), ("acc-none", "nothing_new", 0), ("acc-blocked", "blocked", 1),
                        ("acc-gone", "not_found", 0), ("acc-ghost", "skipped", 0), ("acc-broke", "failed", 0)]
    assert env["attempts"][0]["reason"] == "1 post gagal diproses."
    assert env["attempts"][5]["reason"] == "Drive quota"
    assert all(a["started_at"] <= a["ended_at"] for a in env["attempts"])
    s = result["summary"]
    assert s["posts_collected"] == 4 and s["profiles_blocked"] == 1 and s["items_failed"] == 1
    assert set(s["failures"]) == {"instagram:gone", "instagram:ghost", "instagram:broke"}


@pytest.mark.asyncio
async def test_validate_only_lists_targets_and_harvests_nothing(env):
    env["targets"] = [_target("new", first=True), _target("old")]

    result = await flow_mod.harvest_registry_flow.fn(validate_only=True)

    assert env["harvests"] == [] and env["attempts"] == [] and env["sleeps"] == []
    assert [(d["handle"], d["window_days"]) for d in result["data"]] == [("new", 90), ("old", 31)]


@pytest.mark.asyncio
async def test_a_failed_attempt_write_does_not_stop_the_harvest(env, monkeypatch):
    env["targets"] = [_target("a"), _target("b")]

    async def broken_attempt(**kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(flow_mod, "harvest_attempt_record", broken_attempt)
    result = await flow_mod.harvest_registry_flow.fn()

    assert result["error"] is None
    assert len(env["harvests"]) == 2
    assert "instagram:a (catatan)" in result["summary"]["failures"]


def test_an_unknown_trigger_is_an_error_not_a_crash(env):
    result = asyncio.run(flow_mod.harvest_registry_flow.fn(trigger="weekly"))
    assert "trigger" in result["error"] and env["harvests"] == []
