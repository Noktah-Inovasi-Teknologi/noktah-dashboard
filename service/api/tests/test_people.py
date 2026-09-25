"""People, Units, roles and permissions against real Postgres (US5, G-11, G-15, G-16, migration 012).

Seeded by migration 010: Owner core@noktah.co, Brand Manager Venyu bagas@noktah.co,
Brand Manager Eskala defila@noktah.co. Migration 012 puts the Owner in the Noktah
Unit and gives all three their roles' default permissions.
"""
import pytest

from tests.conftest import add_client, add_person

pytestmark = pytest.mark.schema
OWNER, BM_ESKALA, BM_VENYU = "core@noktah.co", "defila@noktah.co", "bagas@noktah.co"


async def _new(client, name="Rina Baru", emails=("rina@noktah.co", "rina.pribadi@gmail.com"), **extra):
    r = await client.post("/v1/people", json={"display_name": name, "emails": list(emails), **extra})
    assert r.status_code == 200, r.text
    return r.json()


def _form(p, **changes):
    """The profile form as the page sends it, with `changes` applied."""
    body = {"version": p["version"], "display_name": p["display_name"], "jira_account_id": p["jira_account_id"],
            "slack_user_id": p["slack_user_id"], "emails": p["emails"], "units": p["units"],
            "roles": [{"role": r["role"], "brand": r["noktah_brand"]} for r in p["roles"]],
            "permissions": p["permissions"]}
    return body | changes


async def test_brand_manager_grants_only_in_own_unit_and_never_bm(hub_db, api):
    bm = api(BM_ESKALA)
    person = await _new(bm)
    r = await bm.post(f"/v1/people/{person['id']}/roles", json={"role": "account_executive", "brand": "eskala"})
    assert r.status_code == 200 and r.json()["roles"] == [
        {"id": r.json()["roles"][0]["id"], "role": "account_executive", "noktah_brand": "eskala"}]
    assert r.json()["units"] == ["eskala"], "a role puts its holder in the role's Unit"
    assert (await bm.post(f"/v1/people/{person['id']}/roles",
                          json={"role": "production_manager", "brand": "venyu"})).status_code == 403
    r = await bm.post(f"/v1/people/{person['id']}/roles", json={"role": "brand_manager", "brand": "eskala"})
    assert r.status_code == 403 and "Owner" in r.json()["message"]
    assert (await bm.post(f"/v1/people/{person['id']}/roles",
                          json={"role": "owner", "brand": "noktah"})).status_code == 403


async def test_roles_come_from_the_units_catalog(hub_db, api):
    owner = api(OWNER)
    person = await _new(owner, units=["venyu"])
    r = await owner.post(f"/v1/people/{person['id']}/roles", json={"role": "content_editor", "brand": "venyu"})
    assert r.status_code == 422, "Venyu has no Content Editor"
    r = await owner.post(f"/v1/people/{person['id']}/roles", json={"role": "frontend_developer", "brand": "venyu"})
    assert r.status_code == 200
    me = (await owner.get("/v1/me")).json()
    venyu = next(u for u in me["catalog"] if u["key"] == "venyu")
    assert [x["name"] for x in venyu["roles"]] == [
        "Brand Manager", "Production Manager", "Quality Assurance", "Front-end Developer", "Back-end Developer",
        "DevOps", "Mobile Developer"]
    assert [x["key"] for x in next(u for u in me["catalog"] if u["key"] == "noktah")["roles"]] == \
        ["owner", "sales_marketing"]


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
    person = await _new(owner, units=["eskala"], permissions=["hub_access"])
    for email in ("rina@noktah.co", "rina.pribadi@gmail.com"):
        me = (await api(email).get("/v1/me")).json()
        assert me["person"]["id"] == person["id"]


async def test_signing_in_needs_the_hub_access_permission_not_a_role(hub_db, api):
    owner = api(OWNER)
    p = await _new(owner, units=["eskala"], roles=[{"role": "production_manager", "brand": "eskala"}])
    assert (await api("rina@noktah.co").get("/v1/me")).status_code == 403, "a role alone doesn't sign in"
    r = await owner.put(f"/v1/people/{p['id']}", json=_form(p, permissions=["edit_clients"]))
    assert r.status_code == 200 and r.json()["permissions"] == ["hub_access", "edit_clients"], "implied"
    me = (await api("rina@noktah.co").get("/v1/me")).json()
    assert me["can"]["edit_registry"] and not me["can"]["edit_profil"] and me["brands"] == ["eskala"]


async def test_the_noktah_group_reaches_every_brand(hub_db, api):
    owner = api(OWNER)
    await _new(owner, "Sales", ["sales@noktah.co"], units=["noktah"],
               roles=[{"role": "sales_marketing", "brand": "noktah"}], permissions=["hub_access"])
    me = (await api("sales@noktah.co").get("/v1/me")).json()
    assert me["brands"] == ["eskala", "venyu"], "the group itself is not a Client brand"
    assert not me["can"]["edit_registry"]


async def test_leaving_ends_roles_team_and_permissions_and_keeps_history(hub_db, api):
    owner = api(OWNER)
    person = await _new(owner, units=["eskala"], roles=[{"role": "account_executive", "brand": "eskala"}],
                        permissions=["hub_access"])
    async with hub_db.acquire() as conn:
        cid = await add_client(conn, "Klinik Mata Sampang", "eskala")
    v = (await owner.get(f"/v1/clients/{cid}")).json()["version"]
    await owner.put(f"/v1/clients/{cid}/team/account_executive", json={"version": v, "person_id": person["id"]})

    r = await owner.patch(f"/v1/people/{person['id']}", json={"version": 99, "status": "left"})
    assert r.status_code == 409, "stale version refused"
    current = (await owner.get(f"/v1/people/{person['id']}")).json()
    r = await owner.patch(f"/v1/people/{person['id']}", json={"version": current["version"], "status": "left"})
    assert r.status_code == 200 and r.json()["status"] == "left"
    assert r.json()["roles"] == [] and r.json()["permissions"] == []
    assert (await api("rina@noktah.co").get("/v1/me")).status_code == 403
    rec = (await owner.get(f"/v1/clients/{cid}")).json()
    assert rec["team"]["account_executive"] is None
    r = await owner.put(f"/v1/clients/{cid}/team/account_executive",
                        json={"version": rec["version"], "person_id": person["id"]})
    assert r.status_code == 422, "a leaver can't be assigned"
    hist = (await owner.get(f"/v1/clients/{cid}/history")).json()
    assert any(h["field"] == "account_executive" and h["old_value"] == "Rina Baru" for h in hist)
    assert "Rina Baru" in [p["display_name"] for p in (await owner.get("/v1/people?status=left")).json()]


async def test_a_team_slot_takes_only_people_holding_that_role(hub_db, api):
    bm = api(BM_ESKALA)
    editor = await _new(bm, "Editor", ["editor@noktah.co"], units=["eskala"],
                        roles=[{"role": "content_editor", "brand": "eskala"}])
    planner = await _new(bm, "Planner", ["planner@noktah.co"], units=["eskala"],
                         roles=[{"role": "content_planner", "brand": "eskala"}])
    async with hub_db.acquire() as conn:
        cid = await add_client(conn, "Klinik Mata Sampang", "eskala")
    rec = (await bm.get(f"/v1/clients/{cid}")).json()
    assert list(rec["team"]) == ["account_executive", "content_planner", "field_associate", "content_editor",
                                 "quality_assurance"]
    r = await bm.put(f"/v1/clients/{cid}/team/content_editor",
                     json={"version": rec["version"], "person_id": planner["id"]})
    assert r.status_code == 422 and "Planner" in r.json()["message"]
    r = await bm.put(f"/v1/clients/{cid}/team/content_editor",
                     json={"version": rec["version"], "person_id": editor["id"]})
    assert r.status_code == 200 and r.json()["team"]["content_editor"]["name"] == "Editor"

    # Ending the role releases the slot: the team never lists someone without the role.
    editor = (await bm.get(f"/v1/people/{editor['id']}")).json()
    r = await bm.put(f"/v1/people/{editor['id']}", json=_form(editor, roles=[]))
    assert r.status_code == 200 and r.json()["team"] == []
    assert (await bm.get(f"/v1/clients/{cid}")).json()["team"]["content_editor"] is None


async def test_production_manager_cannot_manage_people(hub_db, api):
    async with hub_db.acquire() as conn:
        await add_person(conn, "pm@noktah.co", "PM Eskala", "production_manager", "eskala")
    r = await api("pm@noktah.co").post("/v1/people", json={"display_name": "X", "emails": []})
    assert r.status_code == 403


async def test_create_with_units_roles_and_permissions_at_once(hub_db, api):
    bm = api(BM_ESKALA)
    r = await bm.post("/v1/people", json={
        "display_name": "Sari Ganda", "emails": ["sari@noktah.co"], "units": ["eskala"],
        "roles": [{"role": "field_associate", "brand": "eskala"}, {"role": "content_editor", "brand": "eskala"}],
        "permissions": ["hub_access"]})
    assert r.status_code == 200, r.text
    assert sorted(x["role"] for x in r.json()["roles"]) == ["content_editor", "field_associate"]
    assert r.json()["units"] == ["eskala"] and r.json()["permissions"] == ["hub_access"]
    r = await bm.post("/v1/people", json={"display_name": "Tanpa Unit", "roles": [
        {"role": "field_associate", "brand": "eskala"}]})
    assert r.status_code == 422, "a role needs its Unit on the form"


async def test_one_save_applies_details_emails_units_roles_and_permissions(hub_db, api):
    bm = api(BM_ESKALA)
    p = await _new(bm, units=["eskala"], roles=[{"role": "quality_assurance", "brand": "eskala"}])
    r = await bm.put(f"/v1/people/{p['id']}", json=_form(
        p, display_name="  Rina   Baru Sekali ", jira_account_id="abc",
        emails=["rina@noktah.co", "RINA.KANTOR@noktah.co"],
        roles=[{"role": "field_associate", "brand": "eskala"}, {"role": "content_editor", "brand": "eskala"}],
        permissions=["hub_access", "run_intake"]))
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["display_name"] == "Rina Baru Sekali" and out["jira_account_id"] == "abc"
    assert sorted(out["emails"]) == ["rina.kantor@noktah.co", "rina@noktah.co"]
    assert sorted(x["role"] for x in out["roles"]) == ["content_editor", "field_associate"], "QA ended, two granted"
    assert out["permissions"] == ["hub_access", "run_intake"]
    assert out["version"] > p["version"]
    assert {h["entity"] for h in out["history"]} >= {"person", "person_email", "person_role", "person_permission"}

    r = await bm.put(f"/v1/people/{p['id']}", json=_form(p, display_name="Lagi"))
    assert r.status_code == 409, "stale version refused"


async def test_a_manager_gives_only_permissions_they_hold(hub_db, api):
    owner = api(OWNER)
    await _new(owner, "Pengelola", ["kelola@noktah.co"], units=["eskala"],
               roles=[{"role": "production_manager", "brand": "eskala"}],
               permissions=["hub_access", "manage_people", "edit_clients"])
    manager = api("kelola@noktah.co")
    p = await _new(manager, units=["eskala"])
    assert (await manager.put(f"/v1/people/{p['id']}", json=_form(p, permissions=["edit_clients"]))).status_code == 200
    p = (await manager.get(f"/v1/people/{p['id']}")).json()
    r = await manager.put(f"/v1/people/{p['id']}", json=_form(p, permissions=["approve_guideline"]))
    assert r.status_code == 403
    r = await manager.put(f"/v1/people/{p['id']}", json=_form(p, units=["eskala", "venyu"]))
    assert r.status_code == 403, "Venyu is outside their Units"


async def test_a_refused_role_rolls_the_whole_save_back(hub_db, api):
    bm = api(BM_ESKALA)
    p = await _new(bm)
    r = await bm.put(f"/v1/people/{p['id']}", json=_form(
        p, display_name="Nama Baru", units=["eskala"],
        roles=[{"role": "field_associate", "brand": "eskala"}, {"role": "brand_manager", "brand": "eskala"}]))
    assert r.status_code == 403
    after = (await bm.get(f"/v1/people/{p['id']}")).json()
    assert after["display_name"] == "Rina Baru" and after["roles"] == [] and after["units"] == []
    assert len(after["emails"]) == 2


async def test_brand_manager_cannot_see_people_of_another_unit(hub_db, api):
    async with hub_db.acquire() as conn:
        venyu_pm = await add_person(conn, "pm.venyu@noktah.co", "PM Venyu", "production_manager", "venyu")
    bm = api(BM_ESKALA)
    assert (await bm.get(f"/v1/people/{venyu_pm}")).status_code == 404
    assert venyu_pm not in [p["id"] for p in (await bm.get("/v1/people")).json()]


async def test_people_in_no_unit_are_visible_to_every_manager(hub_db, api):
    async with hub_db.acquire() as conn:
        await add_person(conn, "pm@noktah.co", "PM Eskala", "production_manager", "eskala")
        staff = await add_person(conn, "staff@noktah.co", "Staf Impor", None, None)
    assert staff in [p["id"] for p in (await api("pm@noktah.co").get("/v1/people")).json()]
