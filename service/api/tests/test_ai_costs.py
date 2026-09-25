"""The Biaya AI page's data: spend per case and month, for the Owner and Brand Managers only."""
from datetime import datetime, timezone

import pytest

from app.ai.costs import month_keys
from tests.conftest import add_client, add_person

pytestmark = pytest.mark.schema
OWNER, BM_ESKALA = "core@noktah.co", "defila@noktah.co"


def test_month_keys_run_back_across_a_year_boundary():
    keys = month_keys(datetime(2026, 2, 10, tzinfo=timezone.utc), 4)
    assert keys == ["2025-11", "2025-12", "2026-01", "2026-02"]


async def _seed(hub_db):
    async with hub_db.acquire() as conn:
        cid = await add_client(conn, "Klinik Mata Sampang", "eskala")
        shot = await conn.fetchval(
            """INSERT INTO intakes (client_id, kind, content_hash, status)
               VALUES ($1::uuid, 'image', 'h1', 'ready') RETURNING id""", cid)
        note = await conn.fetchval(
            """INSERT INTO intakes (client_id, kind, content_hash, status)
               VALUES ($1::uuid, 'text', 'h2', 'ready') RETURNING id""", cid)
        await conn.execute(
            """INSERT INTO ai_ledger (call_site, client_id, intake_id, cost_usd) VALUES
               ('hub.summary', $1::uuid, NULL, 0.0003), ('hub.summary', $1::uuid, NULL, 0.0002),
               ('hub.intake', $1::uuid, $2, 0.0004), ('hub.intake', $1::uuid, $3, 0.0001)""", cid, shot, note)
        await conn.execute(
            """INSERT INTO ai_usage (ai_case, call_site, client_name, model, cost_usd) VALUES
               ('generation', 'songbird.generate[Post]', 'Klinik Mata Sampang', 'xiaomi/mimo-v2.6-flash', 0.005),
               ('generation', 'songbird.generate[Post]', 'Klinik Mata Sampang', 'xiaomi/mimo-v2.6-flash', 0.001)""")
        await conn.execute(
            """INSERT INTO ai_usage (ai_case, call_site, cost_usd, at)
               VALUES ('generation', 'songbird.generate[Post]', 0.5, now() - interval '13 months')""")


async def test_a_brand_manager_sees_each_case_by_month(hub_db, api):
    await _seed(hub_db)
    out = (await api(BM_ESKALA).get("/v1/ai/costs")).json()
    this = out["months"][-1]
    assert len(out["months"]) == 12 and this == datetime.now(timezone.utc).strftime("%Y-%m")
    by = {c["key"]: c for c in out["cases"]}
    assert set(by) == {"summary", "intake", "intake_image", "generation", "image", "video"}
    assert by["summary"]["months"][this] == {"calls": 2, "cost_usd": 0.0005}
    assert by["intake"]["this_month"] == 0.0001 and by["intake_image"]["this_month"] == 0.0004
    assert by["generation"]["months"][this]["calls"] == 2, "the 13-month-old row is outside the window"
    assert by["generation"]["total"] == 0.006
    assert by["image"]["total"] == 0 and by["video"]["total"] == 0
    assert out["totals"][this] == pytest.approx(0.007)
    assert out["hub_cap"]["spent_usd"] == pytest.approx(0.001), "the Hub's cap counts ai_ledger only"
    assert by["summary"]["current"] == by["summary"]["models"][0], "hub-api knows its own current model"
    assert by["video"]["current"] is None and by["video"]["label"] and by["video"]["description"]


async def test_only_the_owner_and_brand_managers_see_it(hub_db, api):
    async with hub_db.acquire() as conn:
        await add_person(conn, "pm@noktah.co", "PM Eskala", "production_manager", "eskala")
    pm = api("pm@noktah.co")
    r = await pm.get("/v1/ai/costs")
    assert r.status_code == 403
    assert (await pm.get("/v1/me")).json()["can"]["view_ai_costs"] is False
    assert (await api(BM_ESKALA).get("/v1/me")).json()["can"]["view_ai_costs"] is True
    assert (await api(OWNER).get("/v1/ai/costs")).status_code == 200
