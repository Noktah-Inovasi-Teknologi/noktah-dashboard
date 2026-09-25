"""US1 / SC-009: a signed-in Person with no Manager role reaches no data; Managers see only
their own Noktah Brand's Clients; the Owner sees all. Real Postgres (roles live there)."""
import pytest

from tests.conftest import add_client, add_person

pytestmark = pytest.mark.schema

V1_READS = ["/v1/me", "/v1/clients", "/v1/clients?status=all"]


async def _seed(hub_db):
    async with hub_db.acquire() as conn:
        eskala = await add_client(conn, "Klinik Mata Sampang", "eskala")
        venyu = await add_client(conn, "Venyu Demo Venue", "venyu")
        await add_person(conn, "pm@noktah.co", "PM Eskala", "production_manager", "eskala")
        await add_person(conn, "fa@gmail.com", "Field Associate", "field_associate", "eskala")
        await add_person(conn, "nobody@gmail.com", "No Role", None, None)
    return eskala, venyu


@pytest.mark.parametrize("email", ["stranger@gmail.com", "nobody@gmail.com", "fa@gmail.com"])
async def test_no_manager_role_reaches_nothing(hub_db, api, email):
    eskala, venyu = await _seed(hub_db)
    client = api(email)
    for path in V1_READS + [f"/v1/clients/{eskala}", f"/v1/clients/{venyu}"]:
        r = await client.get(path)
        assert r.status_code == 403, path
        assert r.json()["error"] == "no_access"


async def test_manager_sees_only_own_brand(hub_db, api):
    eskala, venyu = await _seed(hub_db)
    client = api("pm@noktah.co")
    names = [c["name"] for c in (await client.get("/v1/clients")).json()]
    assert names == ["Klinik Mata Sampang"]
    assert (await client.get(f"/v1/clients/{eskala}")).status_code == 200
    r = await client.get(f"/v1/clients/{venyu}")
    assert r.status_code == 404, "another Noktah Brand's Client must not reveal it exists"


async def test_owner_sees_every_brand(hub_db, api):
    await _seed(hub_db)
    client = api("core@noktah.co")  # seeded Owner (migration 010)
    names = {c["name"] for c in (await client.get("/v1/clients")).json()}
    assert names == {"Klinik Mata Sampang", "Venyu Demo Venue"}
    me = (await client.get("/v1/me")).json()
    assert me["can"]["appoint_bm"] and set(me["brands"]) == {"eskala", "venyu"}


async def test_left_person_loses_access(hub_db, api):
    async with hub_db.acquire() as conn:
        await add_person(conn, "left@noktah.co", "Left", "account_executive", "eskala")
        await conn.execute("UPDATE people SET status = 'left' WHERE display_name = 'Left'")
    assert (await api("left@noktah.co").get("/v1/me")).status_code == 403


async def test_person_with_two_emails_is_one_person(hub_db, api):
    async with hub_db.acquire() as conn:
        pid = await add_person(conn, "ae@noktah.co", "AE Two Emails", "account_executive", "eskala")
        await conn.execute("INSERT INTO person_emails (person_id, email) VALUES ($1::uuid, 'ae.personal@gmail.com')", pid)
    a = (await api("ae@noktah.co").get("/v1/me")).json()
    b = (await api("ae.personal@gmail.com").get("/v1/me")).json()
    assert a["person"]["id"] == b["person"]["id"] == pid
