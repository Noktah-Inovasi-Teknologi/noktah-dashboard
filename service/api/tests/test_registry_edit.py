"""Registry editing and the one-time import against real Postgres (US4, FR-010..FR-015, G-19)."""
import pytest

from app.registry.importer import run_import
from tests.conftest import add_client, add_person

pytestmark = pytest.mark.schema


async def _setup(hub_db):
    async with hub_db.acquire() as conn:
        cid = await add_client(conn, "Klinik Mata Sampang", "eskala")
        await add_client(conn, "Ecky Dental Center", "venyu")
        pm = await add_person(conn, "pm@noktah.co", "PM Eskala", "production_manager", "eskala")
        await add_person(conn, "sm@noktah.co", "Sales Eskala", "sales_marketing", None)
        fa = await add_person(conn, "fa@noktah.co", "Nadya", "field_associate", "eskala")
        gone = await add_person(conn, "gone@noktah.co", "Sudah Keluar", None, None)
        await conn.execute("UPDATE people SET status = 'left' WHERE id = $1::uuid", gone)
    return cid, pm, fa, gone


async def test_edit_is_logged_versioned_and_keeps_is_active_in_step(hub_db, api):
    cid, *_ = await _setup(hub_db)
    pm = api("pm@noktah.co")
    rec = (await pm.get(f"/v1/clients/{cid}")).json()
    r = await pm.patch(f"/v1/clients/{cid}", json={"version": rec["version"], "status": "inactive", "quota_post": 6})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "inactive" and r.json()["quotas"]["post"] == 6
    stale = await pm.patch(f"/v1/clients/{cid}", json={"version": rec["version"], "quota_post": 7})
    assert stale.status_code == 409
    async with hub_db.acquire() as conn:
        assert await conn.fetchval("SELECT is_active FROM clients WHERE id = $1::uuid", cid) is False
    hist = (await pm.get(f"/v1/clients/{cid}/history")).json()
    assert {(h["field"], h["old_value"], h["new_value"], h["person"]) for h in hist} >= {
        ("status", "active", "inactive", "PM Eskala"), ("quota_post", None, 6, "PM Eskala")}


async def test_view_only_and_other_brand_are_refused(hub_db, api):
    cid, *_ = await _setup(hub_db)
    rec = (await api("sm@noktah.co").get(f"/v1/clients/{cid}")).json()
    r = await api("sm@noktah.co").patch(f"/v1/clients/{cid}", json={"version": rec["version"], "quota_post": 1})
    assert r.status_code == 403
    async with hub_db.acquire() as conn:
        venyu = await conn.fetchval("SELECT id::text FROM clients WHERE display_name = 'Ecky Dental Center'")
    assert (await api("pm@noktah.co").patch(f"/v1/clients/{venyu}", json={"version": 0})).status_code == 404


async def test_team_takes_active_people_only(hub_db, api):
    cid, _, fa, gone = await _setup(hub_db)
    pm = api("pm@noktah.co")
    v = (await pm.get(f"/v1/clients/{cid}")).json()["version"]
    r = await pm.put(f"/v1/clients/{cid}/team/field_associate", json={"version": v, "person_id": gone})
    assert r.status_code == 422
    r = await pm.put(f"/v1/clients/{cid}/team/field_associate", json={"version": v, "person_id": fa})
    assert r.status_code == 200 and r.json()["team"]["field_associate"]["name"] == "Nadya"
    r = await pm.put(f"/v1/clients/{cid}/team/field_associate", json={"version": r.json()["version"], "person_id": None})
    assert r.json()["team"]["field_associate"] is None
    async with hub_db.acquire() as conn:  # history kept, nothing deleted (G-32)
        assert await conn.fetchval("SELECT count(*) FROM client_team_assignments WHERE client_id = $1::uuid", cid) == 1


async def test_accounts_link_once_and_one_owner(hub_db, api):
    cid, *_ = await _setup(hub_db)
    pm = api("pm@noktah.co")
    async with hub_db.acquire() as conn:
        other = await add_client(conn, "Klinik Mata Bireuen", "eskala")
    v = (await pm.get(f"/v1/clients/{cid}")).json()["version"]
    r = await pm.post(f"/v1/clients/{cid}/accounts", json={"version": v, "platform": "instagram",
                                                           "handle": "https://www.instagram.com/KMSampang/",
                                                           "relation": "own"})
    assert r.status_code == 200
    acc = r.json()["accounts"][0]
    assert acc["handle"] == "kmsampang" and acc["relation"] == "own"
    v2 = (await pm.get(f"/v1/clients/{other}")).json()["version"]
    r = await pm.post(f"/v1/clients/{other}/accounts", json={"version": v2, "platform": "instagram",
                                                             "handle": "@kmsampang", "relation": "own"})
    assert r.status_code == 422 and "Klinik Mata Sampang" in r.json()["message"]
    r = await pm.post(f"/v1/clients/{other}/accounts", json={"version": v2, "platform": "instagram",
                                                             "handle": "@kmsampang", "relation": "competitor"})
    assert r.status_code == 200, "one account may be linked to several Clients"
    v = (await pm.get(f"/v1/clients/{cid}")).json()["version"]
    r = await pm.patch(f"/v1/clients/{cid}/accounts/{acc['account_id']}", json={"version": v, "is_active": False})
    assert r.json()["accounts"][0]["is_active"] is False


async def test_create_client_needs_a_visible_brand(hub_db, api):
    await _setup(hub_db)
    pm = api("pm@noktah.co")
    r = await pm.post("/v1/clients", json={"name": "Klinik Baru", "brand": "eskala", "quota_post": 3})
    assert r.status_code == 200 and r.json()["status"] == "pending" and r.json()["quotas"]["post"] == 3
    assert (await pm.post("/v1/clients", json={"name": "Klinik  baru", "brand": "eskala"})).status_code == 422
    assert (await pm.post("/v1/clients", json={"name": "Lain", "brand": "venyu"})).status_code == 404


# ── import ────────────────────────────────────────────────────────────────────

HEADER = ["No.", "Name", "Folder ID", "Content Plan Folder ID", "Status", "Post", "Story", "Short Video",
          "Total Minutes Equivalent", "Instagram", "TikTok"]
SHEET = [HEADER,
         ["1", "Klinik Mata Sampang", "F1", "CP1", "active", "4", "4", "abc", "720", "https://www.instagram.com/kms/", "-"],
         ["2", "Klinik Utama Gresik", "F2", "CP2", "active", "2", "2", "2", "400", "-", "-"]]
HASH = [["1", "Nadya Safira", "jira-1", "", "1", "Klinik Mata Sampang", "10001", "", "1", "Klinik Mata Sampang",
         "Nadya Safira", "", "1", "Klinik Mata Sampang", "Orang Asing", "", "1", "Klinik Mata Sampang", "KMN",
         "https://www.instagram.com/kmneyecare/"]]


async def _import_setup(hub_db):
    async with hub_db.acquire() as conn:
        # The pre-import state: roster-sync's Clients have no Noktah Brand yet, which
        # migration 011 forbids — it is applied only AFTER the real import.
        await conn.execute("ALTER TABLE clients DROP CONSTRAINT chk_clients_noktah_brand")
        cid = await conn.fetchval(
            "INSERT INTO clients (client_key, display_name) VALUES ('klinik mata sampang', 'Klinik Mata Sampang') "
            "RETURNING id::text")
        await conn.execute("INSERT INTO client_aliases (client_id, alias_key, alias_text, source) "
                           "VALUES ($1::uuid, 'klinik mata sampang', 'Klinik Mata Sampang', 'clients_sheet')", cid)
    return cid


async def test_validate_only_reports_everything_and_writes_nothing(hub_db):
    await _import_setup(hub_db)
    async with hub_db.acquire() as conn:
        before = await conn.fetchval("SELECT count(*) FROM registry_changes")
        report = await run_import(conn, SHEET, HASH, validate_only=True)
        assert await conn.fetchval("SELECT count(*) FROM registry_changes") == before
        assert await conn.fetchval("SELECT count(*) FROM clients") == 1
    assert report["clients"]["created"] == ["Klinik Utama Gresik", "Eskala (internal)"]
    assert {d["field"] for d in report["differences"] if d["client"] == "Klinik Mata Sampang"} >= {
        "status", "quota_post", "drive_folder_id"}
    assert any(s["field"] == "quota_short_video" and s["value"] == "abc" for s in report["skipped"])
    assert any(u["source"] == "FIELD_ASSOCIATE" and u["name"] == "Orang Asing" for u in report["unmatched"])


async def test_real_import_applies_links_and_marks_in_sync(hub_db):
    cid = await _import_setup(hub_db)
    async with hub_db.acquire() as conn:
        report = await run_import(conn, SHEET, HASH, validate_only=False)
        row = await conn.fetchrow("SELECT * FROM clients WHERE id = $1::uuid", cid)
        assert row["status"] == "active" and row["quota_post"] == 4 and row["quota_short_video"] is None
        assert row["jira_component_id"] == "10001" and row["sheet_row_name"] == "Klinik Mata Sampang"
        assert await conn.fetchval("SELECT brand_key FROM noktah_brands WHERE id = $1", row["noktah_brand_id"]) == "eskala"
        assert await conn.fetchval("SELECT is_internal FROM clients WHERE display_name = 'Eskala'") is True
        assert await conn.fetchval(
            """SELECT p.display_name FROM client_team_assignments t JOIN people p ON p.id = t.person_id
               WHERE t.client_id = $1::uuid AND t.team_role = 'content_editor' AND t.valid_to IS NULL""", cid) == \
            "Nadya Safira"
        roles = {r["role"] for r in await conn.fetch(
            "SELECT role FROM client_account_roles WHERE client_id = $1::uuid AND is_active", cid)}
        assert roles == {"owned", "competitor"}
        state = await conn.fetchrow("SELECT * FROM hub_sync_state")
        assert state["last_success_at"] is not None
        assert state["last_change_id"] == await conn.fetchval("SELECT max(id) FROM registry_changes")
        # a second run finds nothing new to do
        again = await run_import(conn, SHEET, HASH, validate_only=True)
    assert report["people"]["created"] == ["Nadya Safira"]
    assert again["differences"] == [] and again["clients"]["created"] == [] and again["team"] == []
