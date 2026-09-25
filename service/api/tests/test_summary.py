"""Summary refresh rules (G-27): confirmed parts only, at most once a day, paused at the cap."""
from datetime import datetime, timedelta, timezone

import pytest

from app.ai.openrouter import AiResult
from app.settings import get_settings
from app.summary import service
from tests.conftest import add_client

pytestmark = pytest.mark.schema
BODY = {"siapa": "Klinik mata di Sampang.", "promo_berjalan": [], "aturan_kunci": ["Sapaan Anda"], "permintaan_terbuka": []}


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
