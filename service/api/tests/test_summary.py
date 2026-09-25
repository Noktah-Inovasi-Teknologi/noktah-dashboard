"""Summary refresh rules (G-27): confirmed parts only, at most once a day, paused at the cap."""
from datetime import datetime, timedelta, timezone

import pytest

from noktah_ai import rotation
from app.ai.openrouter import AiResult
from noktah_ai.rotation import Rotation
from app.settings import get_settings
from app.summary import service
from tests.conftest import add_client

pytestmark = pytest.mark.schema
BODY = {"siapa": "Klinik mata di Sampang.", "promo_berjalan": [], "aturan_kunci": ["Sapaan Anda"], "permintaan_terbuka": []}


@pytest.fixture(autouse=True)
def fresh_rotation(monkeypatch):
    """Rotation state is per process; every test starts on the first model of the repo list."""
    cases = rotation.reset()
    return cases["summary"]


@pytest.fixture
def fake_ai(monkeypatch):
    calls = []

    async def chat_json(**kwargs):
        calls.append(kwargs)
        kwargs["validate"](BODY)
        return AiResult(BODY, "xiaomi/mimo-v2.5", "Xiaomi", 100, 50, 0.001, 1)

    monkeypatch.setattr(service, "chat_json", chat_json)
    return calls


async def _client_with_fact(hub_db):
    async with hub_db.acquire() as conn:
        cid = await add_client(conn, "Klinik Mata Sampang", "eskala")
        pid = await conn.fetchval("SELECT id FROM people LIMIT 1")
        await conn.execute(
            """INSERT INTO card_values (client_id, part, field_key, definition_version, value, state, set_by)
               VALUES ($1::uuid, 'guideline', 'suara', 'v1', $2, 'current', $3)""", cid, {"sapaan": "Anda"}, pid)
    return cid, pid


async def test_empty_card_is_not_summarised(hub_db, fake_ai):
    async with hub_db.acquire() as conn:
        cid = await add_client(conn, "Kosong", "eskala")
        assert await service.refresh_one(conn, cid) == "empty"
    assert fake_ai == []


async def test_refresh_then_fresh_then_once_a_day(hub_db, fake_ai):
    cid, pid = await _client_with_fact(hub_db)
    async with hub_db.acquire() as conn:
        assert await service.refresh_one(conn, cid) == "refreshed"
        assert await service.refresh_one(conn, cid) == "fresh"
        await conn.execute("UPDATE card_values SET value = $2 WHERE client_id = $1::uuid", cid, {"sapaan": "kamu"})
        assert await service.refresh_one(conn, cid) == "too_soon", "changed, but summarised less than 24h ago"
        later = datetime.now(timezone.utc) + timedelta(hours=25)
        assert await service.refresh_one(conn, cid, now=later) == "refreshed"
        view = await service.summary_view(conn, cid)
    assert view["body"] == BODY and view["stale"] is False
    assert len(fake_ai) == 2
    sent = fake_ai[0]["messages"][1]["content"]
    assert "Anda" in sent and "pending" not in sent


async def test_pending_values_never_reach_the_summary(hub_db, fake_ai):
    cid, pid = await _client_with_fact(hub_db)
    async with hub_db.acquire() as conn:
        await conn.execute(
            """INSERT INTO card_values (client_id, part, field_key, definition_version, value, state, set_by)
               VALUES ($1::uuid, 'guideline', 'batasan', 'v1', $2, 'pending', $3)""",
            cid, {"topik_sensitif": ["RAHASIA-PENDING"]}, pid)
        await service.refresh_one(conn, cid)
    assert "RAHASIA-PENDING" not in fake_ai[0]["messages"][1]["content"]


async def test_cap_pauses_and_keeps_last_summary(hub_db, fake_ai, monkeypatch):
    cid, _ = await _client_with_fact(hub_db)
    async with hub_db.acquire() as conn:
        assert await service.refresh_one(conn, cid) == "refreshed"
        await conn.execute("UPDATE card_values SET value = $2 WHERE client_id = $1::uuid", cid, {"sapaan": "Kak"})
        monkeypatch.setenv("HUB_AI_MONTHLY_CAP_USD", "0.0001")
        get_settings.cache_clear()
        later = datetime.now(timezone.utc) + timedelta(hours=25)
        assert await service.refresh_one(conn, cid, now=later) == "paused"
        view = await service.summary_view(conn, cid)
    assert view["stale"] and view["cap_paused"] and view["body"] == BODY
    get_settings.cache_clear()


async def _card(conn, name, pid):
    cid = await add_client(conn, name, "eskala")
    await conn.execute(
        """INSERT INTO card_values (client_id, part, field_key, definition_version, value, state, set_by)
           VALUES ($1::uuid, 'guideline', 'suara', 'v1', $2, 'current', $3)""", cid, {"sapaan": name}, pid)


async def test_repeated_failures_rotate_to_the_next_model(hub_db, monkeypatch):
    from app.ai.openrouter import AiFailure

    monkeypatch.setitem(rotation._rotations, "summary", Rotation("summary", ["first", "second"], rotate_after=2))
    calls = []

    async def first_is_down(**kwargs):
        calls.append(kwargs)
        if kwargs["model"] == "first":
            raise AiFailure("provider_error", "HTTP 429 from DeepInfra (upstream_provider_shared_pool)")
        kwargs["validate"](BODY)
        return AiResult(BODY, "second", "Alibaba", 100, 50, 0.001, 1)

    monkeypatch.setattr(service, "chat_json", first_is_down)
    async with hub_db.acquire() as conn:
        pid = await conn.fetchval("SELECT id FROM people LIMIT 1")
        for name in ("A Klinik", "B Klinik", "C Klinik"):
            await _card(conn, name, pid)
        out = await service.refresh_all(conn)
    assert [c["model"] for c in calls] == ["first", "first", "second"]
    assert out["failed"] == 2 and out["refreshed"] == 1 and out["deferred"] == 0
    assert out["failures"]["A Klinik"] == "first: provider_error: HTTP 429 from DeepInfra (upstream_provider_shared_pool)"
    assert out["model"]["current"] == "second"
    assert calls[0]["rate_limit_backoff"] == service.SUMMARY_BACKOFF


async def test_when_every_model_has_failed_the_rest_wait_for_next_hour(hub_db, monkeypatch):
    from app.ai.openrouter import AiFailure

    monkeypatch.setitem(rotation._rotations, "summary", Rotation("summary", ["first", "second"], rotate_after=1))

    async def all_down(**kwargs):
        raise AiFailure("provider_error", "HTTP 429 from somewhere")

    monkeypatch.setattr(service, "chat_json", all_down)
    async with hub_db.acquire() as conn:
        pid = await conn.fetchval("SELECT id FROM people LIMIT 1")
        for name in ("A Klinik", "B Klinik", "C Klinik"):
            await _card(conn, name, pid)
        out = await service.refresh_all(conn)
    assert out["failed"] == 2 and out["deferred"] == 1, "one turn for each accepted model, then stop"
    assert set(out["failures"]) == {"A Klinik", "B Klinik"}


async def test_other_failures_do_not_stop_the_run(hub_db, monkeypatch):
    from app.ai.openrouter import AiFailure

    async def invalid(**kwargs):
        raise AiFailure("validation_failed", "Kunci wajib tidak ada")

    monkeypatch.setattr(service, "chat_json", invalid)
    async with hub_db.acquire() as conn:
        pid = await conn.fetchval("SELECT id FROM people LIMIT 1")
        for name in ("A Klinik", "B Klinik"):
            await _card(conn, name, pid)
        out = await service.refresh_all(conn)
    assert out["failed"] == 2 and out["deferred"] == 0 and set(out["failures"]) == {"A Klinik", "B Klinik"}
    assert out["failures"]["A Klinik"].startswith("deepseek/deepseek-v4-flash: validation_failed")
