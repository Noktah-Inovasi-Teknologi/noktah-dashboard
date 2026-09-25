"""People and roles against real Postgres (US5, G-9, G-11, G-15, G-16).

Seeded by migration 010: Owner core@noktah.co, Brand Manager Venyu bagas@noktah.co,
Brand Manager Eskala defila@noktah.co.
"""
import pytest

from tests.conftest import add_client, add_person

pytestmark = pytest.mark.schema
OWNER, BM_ESKALA, BM_VENYU = "core@noktah.co", "defila@noktah.co", "bagas@noktah.co"


async def _new(client, name="Rina Baru", emails=("rina@noktah.co", "rina.pribadi@gmail.com")):
    r = await client.post("/v1/people", json={"display_name": name, "emails": list(emails)})
    assert r.status_code == 200, r.text
    return r.json()


async def test_brand_manager_grants_only_in_own_brand_and_never_bm(hub_db, api):
    bm = api(BM_ESKALA)
    person = await _new(bm)
    r = await bm.post(f"/v1/people/{person['id']}/roles", json={"role": "account_executive", "brand": "eskala"})
    assert r.status_code == 200 and r.json()["roles"] == [
        {"id": r.json()["roles"][0]["id"], "role": "account_executive", "noktah_brand": "eskala"}]
    assert (await bm.post(f"/v1/people/{person['id']}/roles",
                          json={"role": "project_manager", "brand": "venyu"})).status_code == 403
    r = await bm.post(f"/v1/people/{person['id']}/roles", json={"role": "brand_manager", "brand": "eskala"})
    assert r.status_code == 403 and "Owner" in r.json()["message"]
    assert (await bm.post(f"/v1/people/{person['id']}/roles", json={"role": "owner"})).status_code == 403


async def test_owner_appoints_one_brand_manager_per_brand(hub_db, api):
    owner = api(OWNER)
    person = await _new(owner, "Calon BM", ["calon@noktah.co"])
    r = await owner.post(f"/v1/people/{person['id']}/roles", json={"role": "brand_manager", "brand": "eskala"})
    assert r.status_code == 422 and "Defila" in r.json()["message"], "one active BM per Noktah Brand"
    defila = next(p for p in (await owner.get("/v1/people")).json() if p["display_name"] == "Defila Priana Falarima")
    bm_role = next(x for x in defila["roles"] if x["role"] == "brand_manager")
    assert (await owner.post(f"/v1/people/{defila['id']}/roles/{bm_role['id']}/end")).status_code == 200
    r = await owner.post(f"/v1/people/{person['id']}/roles", json={"role": "brand_manager", "brand": "eskala"})
    assert r.status_code == 200


async def test_email_belongs_to_one_person_and_the_refusal_names_them(hub_db, api):
    bm = api(BM_ESKALA)
    first = await _new(bm)
    second = await _new(bm, "Orang Lain", ["lain@noktah.co"])
    r = await bm.post(f"/v1/people/{second['id']}/emails", json={"email": " RINA@noktah.co "})
    assert r.status_code == 422 and "Rina Baru" in r.json()["message"]
    assert r.json()["person_id"] == first["id"]
    r = await bm.post("/v1/people", json={"display_name": "Duplikat", "emails": ["rina.pribadi@gmail.com"]})
    assert r.status_code == 422


async def test_every_email_signs_in_as_the_same_person(hub_db, api):
    owner = api(OWNER)
    person = await _new(owner)
    await owner.post(f"/v1/people/{person['id']}/roles", json={"role": "project_manager", "brand": "eskala"})
    for email in ("rina@noktah.co", "rina.pribadi@gmail.com"):
        me = (await api(email).get("/v1/me")).json()
        assert me["person"]["id"] == person["id"]


async def test_leaving_ends_roles_and_team_blocks_sign_in_and_keeps_history(hub_db, api):
    owner = api(OWNER)
    person = await _new(owner)
    await owner.post(f"/v1/people/{person['id']}/roles", json={"role": "project_manager", "brand": "eskala"})
    async with hub_db.acquire() as conn:
        cid = await add_client(conn, "Klinik Mata Sampang", "eskala")
    v = (await owner.get(f"/v1/clients/{cid}")).json()["version"]
    await owner.put(f"/v1/clients/{cid}/team/account_executive", json={"version": v, "person_id": person["id"]})

    r = await owner.patch(f"/v1/people/{person['id']}", json={"version": 99, "status": "left"})
    assert r.status_code == 409, "stale version refused"
    current = (await owner.get(f"/v1/people/{person['id']}")).json()
    r = await owner.patch(f"/v1/people/{person['id']}", json={"version": current["version"], "status": "left"})
    assert r.status_code == 200 and r.json()["status"] == "left" and r.json()["roles"] == []
    assert (await api("rina@noktah.co").get("/v1/me")).status_code == 403
    rec = (await owner.get(f"/v1/clients/{cid}")).json()
    assert rec["team"]["account_executive"] is None
    r = await owner.put(f"/v1/clients/{cid}/team/account_executive",
                        json={"version": rec["version"], "person_id": person["id"]})
    assert r.status_code == 422, "a leaver can't be assigned"
    hist = (await owner.get(f"/v1/clients/{cid}/history")).json()
    assert any(h["field"] == "account_executive" and h["old_value"] == "Rina Baru" for h in hist)
    assert "Rina Baru" in [p["display_name"] for p in (await owner.get("/v1/people?status=left")).json()]


async def test_brand_manager_cannot_edit_people_of_the_other_brand(hub_db, api):
    async with hub_db.acquire() as conn:
        venyu_pm = await add_person(conn, "pm.venyu@noktah.co", "PM Venyu", "project_manager", "venyu")
    bm = api(BM_ESKALA)
    assert (await bm.get(f"/v1/people/{venyu_pm}")).status_code == 404
    assert venyu_pm not in [p["id"] for p in (await bm.get("/v1/people")).json()]


async def test_people_without_roles_are_visible_to_every_manager(hub_db, api):
    async with hub_db.acquire() as conn:
        await add_person(conn, "pm@noktah.co", "PM Eskala", "project_manager", "eskala")
        staff = await add_person(conn, "staff@noktah.co", "Staf Impor", None, None)
    assert staff in [p["id"] for p in (await api("pm@noktah.co").get("/v1/people")).json()]


async def test_project_manager_cannot_manage_people(hub_db, api):
    async with hub_db.acquire() as conn:
        await add_person(conn, "pm@noktah.co", "PM Eskala", "project_manager", "eskala")
    r = await api("pm@noktah.co").post("/v1/people", json={"display_name": "X", "emails": []})
    assert r.status_code == 403
