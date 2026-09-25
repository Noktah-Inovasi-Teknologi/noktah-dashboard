"""Client Card invariants against real Postgres (research R2, G-9, G-12, G-32, FR-044)."""
import pytest

from tests.conftest import add_client, add_person

pytestmark = pytest.mark.schema
PIC = {"nama": "Bu Rina", "jabatan": "Manajer Marketing", "nomor": "0812", "boleh_approve": ["Bu Rina"]}
SRC = {"who": "Bu Rina", "where": "WhatsApp grup", "when": "2026-09-25"}


async def _setup(hub_db):
    async with hub_db.acquire() as conn:
        cid = await add_client(conn, "Klinik Mata Sampang", "eskala")
        await add_person(conn, "pm@noktah.co", "PM Eskala", "production_manager", "eskala")
        await add_person(conn, "sm@noktah.co", "Sales Eskala", "sales_marketing", None)
    return cid


async def _card(client, cid):
    return (await client.get(f"/v1/clients/{cid}/card")).json()


async def test_profil_edit_keeps_history_and_one_current(hub_db, api):
    cid = await _setup(hub_db)
    pm = api("pm@noktah.co")
    v = (await _card(pm, cid))["card_version"]
    r = await pm.put(f"/v1/clients/{cid}/card/profil/pic", json={"card_version": v, "value": PIC, "source": SRC})
    assert r.status_code == 200 and r.json()["state"] == "current"
    v = r.json()["card_version"]
    r = await pm.put(f"/v1/clients/{cid}/card/profil/pic",
                     json={"card_version": v, "value": PIC | {"nomor": "0813"}, "source": SRC})
    assert r.status_code == 200
    hist = (await pm.get(f"/v1/clients/{cid}/card/profil/pic/history")).json()
    assert [h["state"] for h in hist] == ["current", "superseded"]
    assert hist[0]["value"]["nomor"] == "0813" and hist[1]["value"]["nomor"] == "0812"
    async with hub_db.acquire() as conn:
        assert await conn.fetchval(
            "SELECT count(*) FROM card_values WHERE client_id = $1::uuid AND state = 'current'", cid) == 1


async def test_stale_card_version_is_refused(hub_db, api):
    cid = await _setup(hub_db)
    pm = api("pm@noktah.co")
    v = (await _card(pm, cid))["card_version"]
    assert (await pm.put(f"/v1/clients/{cid}/card/profil/pic", json={"card_version": v, "value": PIC})).status_code == 200
    r = await pm.put(f"/v1/clients/{cid}/card/profil/pic", json={"card_version": v, "value": PIC})
    assert r.status_code == 409 and r.json()["error"] == "conflict"


async def test_pm_guideline_edit_waits_for_brand_manager(hub_db, api):
    cid = await _setup(hub_db)
    pm = api("pm@noktah.co")
    v = (await _card(pm, cid))["card_version"]
    r = await pm.put(f"/v1/clients/{cid}/card/guideline/suara", json={"card_version": v, "value": {"sapaan": "Anda"}})
    assert r.json()["state"] == "pending"
    card = await _card(pm, cid)
    assert "suara" not in card["guideline"], "the card keeps showing the approved value (none yet)"
    assert [p["field_key"] for p in card["pending"]] == ["suara"]
    assert (await pm.post(f"/v1/approvals/{r.json()['id']}/approve")).status_code == 403

    bm = api("defila@noktah.co")  # seeded Brand Manager of Eskala
    approvals = (await bm.get("/v1/approvals")).json()
    assert [a["field_key"] for a in approvals] == ["suara"]
    assert (await bm.post(f"/v1/approvals/{approvals[0]['id']}/approve")).status_code == 200
    card = await _card(bm, cid)
    assert card["guideline"]["suara"]["value"] == {"sapaan": "Anda"} and card["pending"] == []


async def test_brand_manager_guideline_edit_is_current_at_once(hub_db, api):
    cid = await _setup(hub_db)
    bm = api("defila@noktah.co")
    v = (await _card(bm, cid))["card_version"]
    r = await bm.put(f"/v1/clients/{cid}/card/guideline/suara", json={"card_version": v, "value": {"sapaan": "kamu"}})
    assert r.json()["state"] == "current"


async def test_owner_approves_when_no_brand_manager(hub_db, api):
    cid = await _setup(hub_db)
    async with hub_db.acquire() as conn:
        await conn.execute("UPDATE person_roles SET valid_to = now() WHERE role = 'brand_manager'")
    pm = api("pm@noktah.co")
    v = (await _card(pm, cid))["card_version"]
    pid = (await pm.put(f"/v1/clients/{cid}/card/guideline/batasan",
                        json={"card_version": v, "value": {"topik_sensitif": ["Harga pesaing"]}})).json()["id"]
    owner = api("core@noktah.co")
    assert (await owner.post(f"/v1/approvals/{pid}/approve")).status_code == 200


async def test_reject_needs_reason_and_keeps_row(hub_db, api):
    cid = await _setup(hub_db)
    pm = api("pm@noktah.co")
    v = (await _card(pm, cid))["card_version"]
    pid = (await pm.put(f"/v1/clients/{cid}/card/guideline/suara",
                        json={"card_version": v, "value": {"sapaan": "Kak"}})).json()["id"]
    bm = api("defila@noktah.co")
    assert (await bm.post(f"/v1/approvals/{pid}/reject", json={"reason": " "})).status_code == 422
    assert (await bm.post(f"/v1/approvals/{pid}/reject", json={"reason": "Klien pakai Anda"})).status_code == 200
    hist = (await bm.get(f"/v1/clients/{cid}/card/guideline/suara/history")).json()
    assert hist[0]["state"] == "rejected" and hist[0]["reject_reason"] == "Klien pakai Anda"


async def test_correction_keeps_the_mistake_marked(hub_db, api):
    cid = await _setup(hub_db)
    pm = api("pm@noktah.co")
    v = (await _card(pm, cid))["card_version"]
    wrong = [{"item": "LASIK", "harga": "Rp 1.550.000", "satuan": "per mata"}]
    v = (await pm.put(f"/v1/clients/{cid}/card/profil/harga_promo", json={"card_version": v, "value": wrong})).json()["card_version"]
    right = [{"item": "LASIK", "harga": "Rp 9.000.000", "satuan": "per mata"}]
    assert (await pm.post(f"/v1/clients/{cid}/card/profil/harga_promo/correct",
                          json={"card_version": v, "value": right})).status_code == 200
    hist = (await pm.get(f"/v1/clients/{cid}/card/profil/harga_promo/history")).json()
    assert [h["state"] for h in hist] == ["current", "corrected"]


async def test_sales_marketing_can_read_not_write(hub_db, api):
    cid = await _setup(hub_db)
    sm = api("sm@noktah.co")
    v = (await _card(sm, cid))["card_version"]
    r = await sm.put(f"/v1/clients/{cid}/card/profil/pic", json={"card_version": v, "value": PIC})
    assert r.status_code == 403


async def test_invalid_shape_is_refused(hub_db, api):
    cid = await _setup(hub_db)
    pm = api("pm@noktah.co")
    v = (await _card(pm, cid))["card_version"]
    r = await pm.put(f"/v1/clients/{cid}/card/profil/pic", json={"card_version": v, "value": {"hobi": "golf"}})
    assert r.status_code == 422


async def test_nothing_is_ever_deleted(hub_db, api):
    cid = await _setup(hub_db)
    pm = api("pm@noktah.co")
    for _ in range(3):
        v = (await _card(pm, cid))["card_version"]
        await pm.put(f"/v1/clients/{cid}/card/profil/pic", json={"card_version": v, "value": PIC})
    async with hub_db.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM card_values WHERE client_id = $1::uuid", cid) == 3


async def test_requests_status_flow_and_reason(hub_db, api):
    cid = await _setup(hub_db)
    pm = api("pm@noktah.co")
    r = (await pm.post(f"/v1/clients/{cid}/requests", json={
        "requested_on": "2026-09-25", "text": "Tolong bikin konten promo LASIK.", "requested_by": "Bu Rina",
        "is_pic": True, "channel": "whatsapp_group"})).json()
    assert r["status"] == "baru"
    bad = await pm.patch(f"/v1/requests/{r['id']}", json={"version": r["version"], "status": "ditolak"})
    assert bad.status_code == 422
    ok = await pm.patch(f"/v1/requests/{r['id']}", json={"version": r["version"], "status": "diproses"})
    assert ok.json()["status"] == "diproses"
    stale = await pm.patch(f"/v1/requests/{r['id']}", json={"version": r["version"], "status": "selesai"})
    assert stale.status_code == 409
    async with hub_db.acquire() as conn:
        events = await conn.fetch("SELECT from_status, to_status FROM client_request_events ORDER BY id")
    assert [(e["from_status"], e["to_status"]) for e in events] == [(None, "baru"), ("baru", "diproses")]
