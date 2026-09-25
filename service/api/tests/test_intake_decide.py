"""Intake end to end against real Postgres, with the model faked (research R3, G-4, G-5, G-26)."""
import base64

import pytest

from app.ai import rotation
from app.ai.openrouter import AiFailure, AiResult
from app.intake import pipeline
from tests.conftest import add_client, add_person

pytestmark = pytest.mark.schema

CHAT = """[25/09/26 10.16] Rina Wati: Mulai 1 Oktober harga LASIK jadi 9,5 jt per mata ya, promo pelajar tetap.
[25/09/26 10.16] Rina Wati: Tolong minggu depan bikin konten promo ini.
[25/09/26 10.20] Andi: Sapaan pakai Kak ya
[25/09/26 10.21] Rina Wati: Pasien Budi Santoso kemarin sudah operasi, hasilnya bagus"""
PIC = {"nama": "Rina Wati", "jabatan": "Marketing", "nomor": "0812", "boleh_approve": []}
PRICE = [{"item": "LASIK", "harga": "9,5 jt", "satuan": "per mata", "syarat": "promo pelajar tetap",
          "berlaku_sampai": ""}]
ANSWER = {
    "items": [
        {"target": "profil", "field_key": "harga_promo", "value": PRICE,
         "excerpt": "Mulai 1 Oktober harga LASIK jadi 9,5 jt per mata ya, promo pelajar tetap.",
         "speaker": "Rina Wati", "spoke_at": "2026-09-25", "valid_until": None, "is_update": True,
         "is_patient_data": False},
        {"target": "request", "field_key": None, "value": "Tolong minggu depan bikin konten promo ini.",
         "excerpt": "Tolong minggu depan bikin konten promo ini.", "speaker": "Rina Wati", "spoke_at": "2026-09-25",
         "valid_until": None, "is_update": False, "is_patient_data": False},
        {"target": "guideline", "field_key": "suara", "value": {"sapaan": "Kak"}, "excerpt": "Sapaan pakai Kak ya",
         "speaker": "Andi", "spoke_at": "2026-09-25", "valid_until": None, "is_update": True,
         "is_patient_data": False},
        {"target": "profil", "field_key": "tentang_usaha", "value": {"ringkasan": "Budi Santoso sudah operasi"},
         "excerpt": "Pasien Budi Santoso kemarin sudah operasi", "speaker": "Rina Wati", "spoke_at": "2026-09-25",
         "valid_until": None, "is_update": False, "is_patient_data": True},
        {"target": "profil", "field_key": "tidak_ada", "value": "x", "excerpt": "Tolong minggu depan",
         "speaker": None, "spoke_at": None, "valid_until": None, "is_update": False, "is_patient_data": False},
    ],
    "no_card_home": [],
    "nothing_found": False,
}


@pytest.fixture(autouse=True)
def fresh_rotation(monkeypatch):
    """Rotation state is per process; every test starts on the first model of the repo list."""
    cases = rotation.load(rotation.models_file("/nonexistent"))
    monkeypatch.setattr(rotation, "_rotations", cases)
    return cases


@pytest.fixture
def fake_ai(monkeypatch):
    state = {"calls": 0, "answer": ANSWER, "fail": None}

    async def chat_json(**kwargs):
        state["calls"] += 1
        state.setdefault("models", []).append(kwargs["model"])
        if state["fail"]:
            raise AiFailure(state["fail"], "fake").spent("m", "p", {"prompt": 10, "completion": 5, "cost": 0.0005})
        kwargs["validate"](state["answer"])
        return AiResult(state["answer"], "xiaomi/mimo-v2.5", "Xiaomi", 3000, 800, 0.0015, 1)

    monkeypatch.setattr(pipeline, "chat_json", chat_json)
    return state


async def _setup(hub_db):
    async with hub_db.acquire() as conn:
        cid = await add_client(conn, "Klinik Mata Sampang", "eskala")
        await add_client(conn, "Klinik Mata Bireuen", "eskala")
        pm = await add_person(conn, "pm@noktah.co", "PM Eskala", "production_manager", "eskala")
        await add_person(conn, "sm@noktah.co", "Sales Eskala", "sales_marketing", None)
        await conn.execute(
            """INSERT INTO card_values (client_id, part, field_key, definition_version, value, state, set_by)
               VALUES ($1::uuid, 'profil', 'pic', 'v1', $2, 'current', $3::uuid)""", cid, PIC, pm)
    return cid


async def _submit(client, cid, text=CHAT):
    return await client.post(f"/v1/clients/{cid}/intakes", json={"kind": "text", "text": text})


def _by_target(intake):
    return {(p["target"], p["field_key"]): p for p in intake["proposals"]}


async def _version(client, cid):
    return (await client.get(f"/v1/clients/{cid}/card")).json()["card_version"]


async def test_one_paste_gives_fact_and_request_and_drops_patient_data(hub_db, api, fake_ai):
    cid = await _setup(hub_db)
    pm = api("pm@noktah.co")
    r = await _submit(pm, cid)
    assert r.status_code == 200, r.text
    intake = r.json()
    assert intake["status"] == "ready" and not intake["cached"]
    assert intake["dropped"] == {"patient_data": 1, "invalid": 1, "unchanged": 0}
    got = _by_target(intake)
    assert set(got) == {("profil", "harga_promo"), ("request", None), ("guideline", "suara")}
    assert got[("profil", "harga_promo")]["flags"] == []
    assert got[("guideline", "suara")]["flags"] == ["not_pic"]
    async with hub_db.acquire() as conn:
        # nothing is on the card until a Manager decides (FR-035)
        assert await conn.fetchval(
            "SELECT count(*) FROM card_values WHERE client_id = $1::uuid AND field_key = 'harga_promo'", cid) == 0
        assert await conn.fetchval("SELECT count(*) FROM intake_proposals WHERE excerpt LIKE '%Budi%'") == 0
        assert float(await conn.fetchval("SELECT sum(cost_usd) FROM ai_ledger WHERE call_site = 'hub.intake'")) == 0.0015

    v = await _version(pm, cid)
    r = await pm.post(f"/v1/proposals/{got[('profil', 'harga_promo')]['id']}/decide",
                      json={"outcome": "accept", "ticks": {}, "card_version": v})
    assert r.status_code == 200, r.text
    assert r.json()["card"]["state"] == "current" and r.json()["proposal"]["outcome"] == "accepted"
    r = await pm.post(f"/v1/proposals/{got[('request', None)]['id']}/decide", json={"outcome": "accept", "ticks": {}})
    req = r.json()["request"]
    assert req["text"] == "Tolong minggu depan bikin konten promo ini." and req["status"] == "baru"
    assert req["is_pic"] is True and req["channel"] == "whatsapp_group" and req["requested_on"] == "2026-09-25"

    card = (await pm.get(f"/v1/clients/{cid}/card")).json()
    assert card["profil"]["harga_promo"]["value"] == PRICE
    assert card["profil"]["harga_promo"]["from_intake"] == intake["id"]


async def test_ticks_are_enforced_and_pm_guideline_goes_pending(hub_db, api, fake_ai):
    cid = await _setup(hub_db)
    pm = api("pm@noktah.co")
    suara = _by_target((await _submit(pm, cid)).json())[("guideline", "suara")]
    v = await _version(pm, cid)
    r = await pm.post(f"/v1/proposals/{suara['id']}/decide", json={"outcome": "accept", "ticks": {}, "card_version": v})
    assert r.status_code == 422 and "PIC sudah konfirmasi" in r.json()["message"]
    r = await pm.post(f"/v1/proposals/{suara['id']}/decide",
                      json={"outcome": "accept", "ticks": {"pic_confirmed": True}, "card_version": v})
    assert r.status_code == 200 and r.json()["card"]["state"] == "pending"
    assert r.json()["proposal"]["ticks"] == {"pic_confirmed": True}
    # decided once only
    r = await pm.post(f"/v1/proposals/{suara['id']}/decide", json={"outcome": "reject", "ticks": {}})
    assert r.status_code == 409


async def test_unverified_quote_must_be_edited(hub_db, api, fake_ai):
    cid = await _setup(hub_db)
    fake_ai["answer"] = {**ANSWER, "items": [dict(ANSWER["items"][0], excerpt="harga LASIK jadi 9 jt per mata")]}
    pm = api("pm@noktah.co")
    p = (await _submit(pm, cid)).json()["proposals"][0]
    assert "unverified" in p["flags"]
    v = await _version(pm, cid)
    r = await pm.post(f"/v1/proposals/{p['id']}/decide", json={"outcome": "accept", "ticks": {}, "card_version": v})
    assert r.status_code == 422
    edited = [dict(PRICE[0], harga="9,5 jt")]
    r = await pm.post(f"/v1/proposals/{p['id']}/decide",
                      json={"outcome": "edit", "final_value": edited, "ticks": {}, "card_version": v})
    assert r.status_code == 200 and r.json()["proposal"]["outcome"] == "edited"
    assert r.json()["proposal"]["final_value"] == edited


async def test_identical_paste_returns_the_earlier_intake(hub_db, api, fake_ai):
    cid = await _setup(hub_db)
    pm = api("pm@noktah.co")
    first = (await _submit(pm, cid)).json()
    again = (await _submit(pm, cid, CHAT.replace("\n", "\r\n") + "\n\n\n")).json()
    assert again["id"] == first["id"] and again["cached"] is True
    assert fake_ai["calls"] == 1


async def test_failed_ai_is_recorded_not_shown_and_can_be_retried(hub_db, api, fake_ai):
    cid = await _setup(hub_db)
    pm = api("pm@noktah.co")
    fake_ai["fail"] = "validation_failed"
    r = (await _submit(pm, cid)).json()
    assert r["status"] == "failed" and r["failure_reason"] == "validation_failed" and r["proposals"] == []
    async with hub_db.acquire() as conn:
        assert float(await conn.fetchval("SELECT sum(cost_usd) FROM ai_ledger")) == 0.0005  # still billed
    fake_ai["fail"] = None
    retry = (await _submit(pm, cid)).json()
    assert retry["id"] != r["id"] and retry["status"] == "ready"


async def test_cap_returns_402_and_stores_nothing(hub_db, api, fake_ai):
    cid = await _setup(hub_db)
    async with hub_db.acquire() as conn:
        await conn.execute("INSERT INTO ai_ledger (call_site, model, cost_usd) VALUES ('hub.summary', 'm', 5.0)")
    pm = api("pm@noktah.co")
    r = await _submit(pm, cid)
    assert r.status_code == 402 and r.json()["error"] == "ai_cap_reached"
    assert fake_ai["calls"] == 0
    async with hub_db.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM intakes") == 0
    usage = (await pm.get("/v1/ai/usage")).json()
    assert usage["paused"] is True and usage["cap_usd"] == 5.0


async def test_view_only_roles_cannot_run_intake(hub_db, api, fake_ai):
    cid = await _setup(hub_db)
    r = await _submit(api("sm@noktah.co"), cid)
    assert r.status_code == 403
    assert fake_ai["calls"] == 0


async def test_screenshot_proposals_need_the_manual_check_tick(hub_db, api, fake_ai):
    cid = await _setup(hub_db)
    fake_ai["answer"] = {**ANSWER, "items": [ANSWER["items"][0]]}
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32).decode()
    pm = api("pm@noktah.co")
    r = await pm.post(f"/v1/clients/{cid}/intakes", json={"kind": "image", "image_base64": png, "mime": "image/png"})
    p = r.json()["proposals"][0]
    assert p["flags"] == ["from_image"]
    v = await _version(pm, cid)
    r = await pm.post(f"/v1/proposals/{p['id']}/decide", json={"outcome": "accept", "ticks": {}, "card_version": v})
    assert r.status_code == 422 and "Sudah dicek manual" in r.json()["message"]
    r = await pm.post(f"/v1/proposals/{p['id']}/decide",
                      json={"outcome": "accept", "ticks": {"image_checked": True}, "card_version": v})
    assert r.status_code == 200


async def test_purge_removes_raw_material_but_keeps_excerpts(hub_db, api, fake_ai, monkeypatch):
    cid = await _setup(hub_db)
    pm = api("pm@noktah.co")
    intake = (await _submit(pm, cid)).json()
    async with hub_db.acquire() as conn:
        await conn.execute("UPDATE intakes SET submitted_at = now() - interval '13 months' WHERE id = $1::uuid",
                           intake["id"])
    from app.settings import get_settings
    monkeypatch.setattr(get_settings(), "internal_token", "t0k")
    r = await pm.post("/internal/intakes/purge-raw", headers={"X-Hub-Internal-Token": "t0k"})
    assert r.json() == {"purged": 1}
    after = (await pm.get(f"/v1/intakes/{intake['id']}")).json()
    assert after["raw_available"] is False
    assert [p["excerpt"] for p in after["proposals"]] == [p["excerpt"] for p in intake["proposals"]]


async def test_accepting_a_partial_proposal_keeps_edits_made_since(hub_db, api, fake_ai):
    cid = await _setup(hub_db)
    excerpt = "Mulai 1 Oktober harga LASIK jadi 9,5 jt per mata ya, promo pelajar tetap."
    fake_ai["answer"] = {**ANSWER, "items": [
        {"target": "profil", "field_key": "tentang_usaha.visi_misi", "value": "Promo pelajar tetap", "excerpt": excerpt,
         "speaker": "Rina Wati", "spoke_at": None, "valid_until": None, "is_update": False, "is_patient_data": False},
        {"target": "profil", "field_key": "tentang_usaha", "value": {"industri": "Kesehatan mata"}, "excerpt": excerpt,
         "speaker": "Rina Wati", "spoke_at": None, "valid_until": None, "is_update": False, "is_patient_data": False}]}
    pm = api("pm@noktah.co")
    proposals = (await _submit(pm, cid)).json()["proposals"]
    assert len(proposals) == 1, "parts of one field are one Proposal"
    # Someone edits the same field directly before the Proposal is decided.
    v = await _version(pm, cid)
    r = await pm.put(f"/v1/clients/{cid}/card/profil/tentang_usaha",
                     json={"card_version": v, "value": {"ringkasan": "Klinik mata di Sampang"}})
    assert r.status_code == 200
    shown = (await pm.get(f"/v1/intakes/{(await pm.get(f'/v1/clients/{cid}/intakes')).json()[0]['id']}")).json()
    assert shown["proposals"][0]["proposed_value"] == {
        "ringkasan": "Klinik mata di Sampang", "visi_misi": "Promo pelajar tetap", "industri": "Kesehatan mata"}
    r = await pm.post(f"/v1/proposals/{proposals[0]['id']}/decide",
                      json={"outcome": "accept", "ticks": {}, "card_version": r.json()["card_version"]})
    assert r.status_code == 200, r.text
    card = (await pm.get(f"/v1/clients/{cid}/card")).json()
    assert card["profil"]["tentang_usaha"]["value"] == {
        "ringkasan": "Klinik mata di Sampang", "visi_misi": "Promo pelajar tetap", "industri": "Kesehatan mata"}


async def test_text_and_screenshots_use_their_own_model_lists(hub_db, api, fake_ai, fresh_rotation):
    cid = await _setup(hub_db)
    pm = api("pm@noktah.co")
    assert (await _submit(pm, cid)).status_code == 200
    png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32).decode()
    assert (await pm.post(f"/v1/clients/{cid}/intakes",
                          json={"kind": "image", "image_base64": png, "mime": "image/png"})).status_code == 200
    assert fake_ai["models"] == [fresh_rotation["intake"].models[0], fresh_rotation["intake_image"].models[0]]


async def test_repeated_intake_failures_move_to_the_next_model(hub_db, api, fake_ai, fresh_rotation):
    cid = await _setup(hub_db)
    pm = api("pm@noktah.co")
    fake_ai["fail"] = "provider_error"
    first, second = fresh_rotation["intake"].models[:2]
    for i in range(fresh_rotation["intake"].rotate_after):
        await _submit(pm, cid, text=f"{CHAT} {i}")
    fake_ai["fail"] = None
    await _submit(pm, cid, text=f"{CHAT} ok")
    assert fake_ai["models"][-1] == second and fake_ai["models"][0] == first
