"""Failure alerts ("No silent failures"): what counts as a problem, and that the
hook posts exactly when there is one. No network: the Slack POST is replaced."""
import pytest
from prefect import flow

from flows.common import alerts


# --- find_problems: the flows' real return shapes -----------------------------

def test_healthy_result_has_no_problems():
    assert alerts.find_problems({"error": None, "summary": {"items_collected": 12}}) == []


def test_top_level_error_is_reported():
    problems = alerts.find_problems({"error": "Sheet 'Clients' not found", "summary": {}})
    assert problems == ["Penyebab: Sheet 'Clients' not found"]


def test_songbird_batch_per_client_failures_are_each_reported():
    result = {"error": None, "summary": {"failures": {"Klinik A": "no quota", "Klinik B": "timeout"}}}
    assert alerts.find_problems(result) == ["Klinik A: no quota", "Klinik B: timeout"]


def test_one_blocked_profile_warns_without_ip_hint(monkeypatch):
    monkeypatch.delenv("HARVEST_ROTATE_IP_THRESHOLD", raising=False)
    problems = alerts.find_problems({"summary": {"profiles_blocked": 1}})
    assert len(problems) == 1 and "diblokir" in problems[0]


def test_several_blocked_profiles_add_the_ip_rotation_hint(monkeypatch):
    monkeypatch.delenv("HARVEST_ROTATE_IP_THRESHOLD", raising=False)
    problems = alerts.find_problems({"summary": {"profiles_blocked": 2, "items_failed": 3}})
    assert any("ganti IP hotspot" in p for p in problems)
    assert "3 konten gagal diproses." in problems


def test_non_dict_result_is_not_a_problem():
    assert alerts.find_problems(None) == []
    assert alerts.find_problems("done") == []


def test_long_errors_are_clipped_to_one_line():
    problems = alerts.find_problems({"error": "x\n" * 1000})
    assert "\n" not in problems[0] and len(problems[0]) <= len("Penyebab: ") + alerts.MAX_ERROR_CHARS


# --- message ------------------------------------------------------------------

def test_message_names_the_flow_client_and_links_the_run():
    text = alerts.build_message(
        "songbird-monthly-plan", "hospitable-cat", "http://x/runs/flow-run/1",
        ["Penyebab: no quota"], subject="Ecky Dental Center",
    )
    assert text.startswith("⚠️ *songbird-monthly-plan* (Ecky Dental Center)")
    assert "<http://x/runs/flow-run/1|hospitable-cat>" in text


def test_subject_prefers_client_then_profiles():
    assert alerts._subject({"client": "MCafe"}) == "MCafe"
    assert alerts._subject({"profiles": ["https://www.instagram.com/lasikasyik/"]}) == "lasikasyik"
    assert alerts._subject({}) is None


def test_unknown_channel_is_rejected_at_decoration_time():
    with pytest.raises(ValueError):
        alerts.alert_hooks("marketing")


# --- the hook end to end, with Slack replaced -----------------------------------

@pytest.fixture
def sent(monkeypatch):
    calls = []

    async def fake_post(channel, text):
        calls.append((channel, text))
        return True

    monkeypatch.setattr(alerts, "post_to_slack", fake_post)
    return calls


@pytest.mark.asyncio
async def test_hook_posts_when_a_flow_returns_an_error(sent):
    @flow(name="alert-test-error", **alerts.alert_hooks())
    async def f(client: str = "Klinik A"):
        return {"error": "sheet missing", "summary": {}}

    await f()
    assert len(sent) == 1
    channel, text = sent[0]
    assert channel == "eskala"
    assert "(Klinik A)" in text and "sheet missing" in text and text.startswith("⚠️")


@pytest.mark.asyncio
async def test_hook_is_silent_on_a_healthy_run(sent):
    @flow(name="alert-test-ok", **alerts.alert_hooks())
    async def f():
        return {"error": None, "summary": {"items_collected": 5}}

    await f()
    assert sent == []


@pytest.mark.asyncio
async def test_hook_posts_when_a_flow_raises(sent):
    @flow(name="alert-test-raise", **alerts.alert_hooks("noktah"))
    async def f():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await f()
    assert len(sent) == 1
    channel, text = sent[0]
    assert channel == "noktah" and text.startswith("❌") and "boom" in text


@pytest.mark.asyncio
async def test_missing_webhook_variable_posts_nothing_and_does_not_raise(monkeypatch):
    monkeypatch.delenv("SLACK_AUTOMATION_ESKALA", raising=False)
    assert await alerts.post_to_slack("eskala", "hello") is False
