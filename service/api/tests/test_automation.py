"""Otomasi end to end in hub-api (spec 009 US1–US4): permissions, plan scans, Greenlight,
batches that never duplicate, the watcher, harvest targets and review marks.
Prefect is never called: `prefect_api.run_deployment` is replaced in each test."""
import pytest

from app import prefect_api
from tests.conftest import add_client, add_person

pytestmark = pytest.mark.schema
OWNER, BM = "core@noktah.co", "defila@noktah.co"
INTERNAL = {"x-hub-internal-token": "tok"}


@pytest.fixture
def started(monkeypatch):
    runs = []

    async def fake(name, parameters):
        runs.append((name, parameters))
        return f"run-{len(runs)}"
    monkeypatch.setattr(prefect_api, "run_deployment", fake)
    return runs


@pytest.fixture
def internal_token(monkeypatch):
    monkeypatch.setenv("HUB_API_INTERNAL_TOKEN", "tok")
    from app.settings import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def rows(n=3, over=None):
    over = over or {}
    out = []
    for i in range(2, 2 + n):
        cells = {"No.": str(i - 1), "Tanggal": f"{i:02d}/10/2026", "Bentuk": "Post", "Topik": f"Topik {i}",
                 "Caption": "c"}
        cells.update(over.get(i, {}))
        out.append({"row_number": i, "cells": cells})
    return out


async def _client_with_team(conn, name="Pelita Delapan"):
    cid = await add_client(conn, name, "eskala")
    fa = await add_person(conn, f"fa-{name[:3].lower()}@x.co", "Sofi", "field_associate", "eskala")
    ce = await add_person(conn, f"ce-{name[:3].lower()}@x.co", "Nadya", "content_editor", "eskala")
    await conn.execute("UPDATE people SET jira_account_id = 'acc-fa' WHERE id = $1::uuid", fa)
    await conn.execute("UPDATE people SET jira_account_id = 'acc-ce' WHERE id = $1::uuid", ce)
    for role, pid in (("field_associate", fa), ("content_editor", ce)):
        await conn.execute("INSERT INTO client_team_assignments (client_id, team_role, person_id) VALUES ($1::uuid, $2, $3::uuid)",
                           cid, role, pid)
    await conn.execute("UPDATE clients SET jira_component_id = '10100', content_plan_folder_id = 'folder', quota_post = 3 WHERE id = $1::uuid", cid)
    return cid


async def _scan(api, cid, body_rows, month="2026-10"):
    r = await api(OWNER).post("/internal/content-plans/scan", headers=INTERNAL, json={
        "client_id": cid, "month": month, "state": "found", "drive_file_id": "file1",
        "file_name": "Content Plan - Pelita Delapan - Oktober 2026", "tab_name": "Sheet1", "rows": body_rows})
    assert r.status_code == 200, r.text
    return r.json()


async def test_permissions_gate_otomasi_laporan_and_incentive(hub_db, api):
    async with hub_db.acquire() as conn:
        await add_person(conn, "ae@x.co", "AE", "account_executive", "eskala")
    me = (await api(BM).get("/v1/me")).json()["can"]
    assert me["manage_automation"] and me["view_reports"] and me["view_incentive"]
    ae = (await api("ae@x.co").get("/v1/me")).json()["can"]
    assert not (ae["manage_automation"] or ae["view_reports"] or ae["view_incentive"])
    assert (await api("ae@x.co").get("/v1/content-plans?month=2026-10")).status_code == 403
    assert (await api("ae@x.co").get("/v1/reports/delivery?month=2026-10")).status_code == 403
    assert (await api("ae@x.co").get("/v1/reports/incentive?month=2026-10")).status_code == 403


async def test_greenlight_then_batch_never_duplicates(hub_db, api, started, internal_token):
    async with hub_db.acquire() as conn:
        cid = await _client_with_team(conn)
    plan_id = (await _scan(api, cid, rows()))["plan_id"]
    plan = (await api(BM).get(f"/v1/content-plans/{plan_id}")).json()
    assert plan["blocking"] == [] and plan["can_greenlight"] and not plan["can_create"]
    assert plan["quota"]["Post"] == {"planned": 3, "quota": 3}

    # a stale fingerprint is refused
    r = await api(BM).post(f"/v1/content-plans/{plan_id}/greenlight", json={"fingerprint": "old"})
    assert r.status_code == 409
    r = await api(BM).post(f"/v1/content-plans/{plan_id}/greenlight", json={"fingerprint": plan["fingerprint"]})
    assert r.status_code == 200 and r.json()["status"] == "greenlit" and r.json()["can_create"]

    r = await api(BM).post("/v1/jira-batches", json={"plan_ids": [plan_id]})
    assert r.status_code == 202, r.text
    batch = r.json()["batch"]
    assert started == [(prefect_api.JIRA_CREATE, {"batch_id": batch["id"]})] and batch["total"] == 3

    claim = (await api(OWNER).post("/internal/jira-batches/claim", headers=INTERNAL, json={"batch_id": batch["id"]})).json()
    [p] = claim["plans"]
    assert p["component_id"] == "10100" and p["field_associate_account"] == "acc-fa"
    assert [r["row_number"] for r in p["rows_to_create"]] == [2, 3, 4] and p["refused"] is None

    # two created, one rejected by Jira
    result = [{"plan_id": plan_id, "row_number": 2, "outcome": "created", "issue_key": "ESKL-1",
               "cells": p["rows_to_create"][0]["cells"]},
              {"plan_id": plan_id, "row_number": 3, "outcome": "created", "issue_key": "ESKL-2",
               "cells": p["rows_to_create"][1]["cells"]},
              {"plan_id": plan_id, "row_number": 4, "outcome": "failed", "reason": "summary invalid"}]
    await api(OWNER).post("/internal/jira-batches/result", headers=INTERNAL,
                          json={"batch_id": batch["id"], "rows": result, "done": True})
    got = (await api(BM).get(f"/v1/jira-batches/{batch['id']}")).json()
    assert (got["status"], got["created"], got["failed"]) == ("done", 2, 1)

    # the keys land in the sheet; the next scan sees them; only row 4 is left
    keyed = rows(3, {2: {"Key": "ESKL-1"}, 3: {"Key": "ESKL-2"}})
    await _scan(api, cid, keyed)
    plan = (await api(BM).get(f"/v1/content-plans/{plan_id}")).json()
    assert plan["status"] == "partly_created" and plan["rows_without_issue"] == 1 and plan["can_create"]
    r = await api(BM).post("/v1/jira-batches", json={"plan_ids": [plan_id]})
    batch2 = r.json()["batch"]
    claim = (await api(OWNER).post("/internal/jira-batches/claim", headers=INTERNAL, json={"batch_id": batch2["id"]})).json()
    assert [r["row_number"] for r in claim["plans"][0]["rows_to_create"]] == [4]

    # even if the Key write had failed, a row with a recorded issue is never created again
    await api(OWNER).post("/internal/jira-batches/result", headers=INTERNAL, json={
        "batch_id": batch2["id"], "done": True,
        "rows": [{"plan_id": plan_id, "row_number": 4, "outcome": "created", "issue_key": "ESKL-3",
                  "cells": claim["plans"][0]["rows_to_create"][0]["cells"]}]})
    await _scan(api, cid, keyed)  # row 4 still has no Key in the sheet
    plan = (await api(BM).get(f"/v1/content-plans/{plan_id}")).json()
    assert [(f["kind"], f["issue_key"]) for f in plan["flags"]] == [("key_erased", "ESKL-3")]
    r = await api(BM).post("/v1/jira-batches", json={"plan_ids": [plan_id]})
    assert r.status_code == 422 and "Key yang terhapus" in r.text


async def test_a_plan_changed_after_greenlight_needs_a_new_one(hub_db, api, started, internal_token):
    async with hub_db.acquire() as conn:
        cid = await _client_with_team(conn)
    plan_id = (await _scan(api, cid, rows()))["plan_id"]
    fp = (await api(BM).get(f"/v1/content-plans/{plan_id}")).json()["fingerprint"]
    await api(BM).post(f"/v1/content-plans/{plan_id}/greenlight", json={"fingerprint": fp})
    await _scan(api, cid, rows(3, {3: {"Topik": "Diganti klien"}}))
    plan = (await api(BM).get(f"/v1/content-plans/{plan_id}")).json()
    assert plan["status"] == "changed_since_greenlight" and not plan["can_create"] and plan["can_greenlight"]
    r = await api(BM).post("/v1/jira-batches", json={"plan_ids": [plan_id]})
    assert r.status_code == 422 and "Greenlight ulang" in r.text
    assert started == []


async def test_blocking_problems_stop_a_greenlight(hub_db, api, internal_token):
    async with hub_db.acquire() as conn:
        cid = await add_client(conn, "Tanpa Tim", "eskala")
    plan_id = (await _scan(api, cid, rows()))["plan_id"]
    plan = (await api(BM).get(f"/v1/content-plans/{plan_id}")).json()
    assert {b["code"] for b in plan["blocking"]} >= {"no_field_associate", "no_content_editor", "no_component"}
    r = await api(BM).post(f"/v1/content-plans/{plan_id}/greenlight", json={"fingerprint": plan["fingerprint"]})
    assert r.status_code == 422


async def test_the_watcher_flags_and_queues_one_comment(hub_db, api, started, internal_token):
    async with hub_db.acquire() as conn:
        cid = await _client_with_team(conn)
        plan_id = (await _scan(api, cid, rows(2, {2: {"Key": "ESKL-7"}})))["plan_id"]
        await conn.execute(
            """INSERT INTO content_plan_issues (issue_key, plan_id, row_number, fingerprint, cells, last_fingerprint)
               SELECT 'ESKL-7', $1::uuid, 2, '', rows->0->'cells', NULL FROM content_plans WHERE id = $1::uuid""", plan_id)
        from app.automation import plans as rules
        cells = (await conn.fetchval("SELECT rows->0->'cells' FROM content_plans WHERE id = $1::uuid", plan_id))
        await conn.execute("UPDATE content_plan_issues SET fingerprint = $1 WHERE issue_key = 'ESKL-7'",
                           rules.row_fingerprint(cells))
    out = await _scan(api, cid, rows(2, {2: {"Key": "ESKL-7", "Tanggal": "22/10/2026"}}))
    [c] = out["comments"]
    assert c["issue_key"] == "ESKL-7" and "02/10/2026 → 22/10/2026" in c["text"]
    await api(OWNER).post("/internal/content-plans/comments", headers=INTERNAL,
                          json={"results": [{"flag_id": c["flag_id"], "ok": True}]})
    again = await _scan(api, cid, rows(2, {2: {"Key": "ESKL-7", "Tanggal": "22/10/2026"}}))
    assert again["comments"] == [], "the same change is commented once"
    plan = (await api(BM).get(f"/v1/content-plans/{plan_id}")).json()
    assert plan["status"] == "changed_after_issues" and plan["flags"][0]["comment_state"] == "posted"
    r = await api(BM).post(f"/v1/content-plans/flags/{plan['flags'][0]['id']}/resolve")
    assert r.status_code == 200 and r.json()["flags"] == []


async def test_harvest_targets_follow_the_registry(hub_db, api, started, internal_token):
    async with hub_db.acquire() as conn:
        a = await add_client(conn, "Klinik A", "eskala")
        v = await add_client(conn, "Venyu App", "venyu")
        ids = {}
        for handle, client, role in (("klinika", a, "owned"), ("kompetitor", a, "competitor"), ("venyuapp", v, "owned")):
            acc = await conn.fetchval("INSERT INTO accounts (platform) VALUES ('instagram') RETURNING id::text")
            await conn.execute("""INSERT INTO account_handles (account_id, platform, handle_key, handle_text, is_current, identifier_kind)
                                  VALUES ($1::uuid, 'instagram', $2, $2, true, 'handle')""", acc, handle)
            await conn.execute("""INSERT INTO client_account_roles (client_id, account_id, role, is_active)
                                  VALUES ($1::uuid, $2::uuid, $3, true)""", client, acc, role)
            ids[handle] = acc
        await conn.execute(
            """INSERT INTO harvest_attempts (account_id, trigger, window_days, started_at, outcome, posts_collected)
               VALUES ($1::uuid, 'monthly', 90, now(), 'collected', 12)""", ids["klinika"])
    t = (await api(OWNER).post("/internal/harvest/targets", headers=INTERNAL, json={})).json()["targets"]
    by = {x["handle"]: x for x in t}
    assert set(by) == {"klinika", "kompetitor"}, "Venyu's accounts are not harvested"
    assert by["klinika"]["window_days"] == 31 and by["kompetitor"]["window_days"] == 90
    assert by["kompetitor"]["url"] == "https://www.instagram.com/kompetitor/"
    listed = (await api(BM).get("/v1/harvest/accounts")).json()["accounts"]
    assert {(x["handle"], x["role"], x["status"]) for x in listed} == {("klinika", "own", "ok"),
                                                                        ("kompetitor", "competitor", "never")}
    r = await api(BM).post(f"/v1/harvest/accounts/{ids['kompetitor']}/run")
    assert r.status_code == 202 and started[-1] == (prefect_api.HARVEST,
                                                    {"account_ids": [ids["kompetitor"]], "trigger": "manual"})
    assert (await api(BM).post(f"/v1/harvest/accounts/{ids['venyuapp']}/run")).status_code == 404


async def test_review_marks_keep_history_and_the_ad_flag_in_step(hub_db, api):
    async with hub_db.acquire() as conn:
        a = await add_client(conn, "Klinik A", "eskala")
        acc = await conn.fetchval("INSERT INTO accounts (platform) VALUES ('instagram') RETURNING id::text")
        await conn.execute("""INSERT INTO account_handles (account_id, platform, handle_key, handle_text, is_current, identifier_kind)
                              VALUES ($1::uuid, 'instagram', 'klinika', 'klinika', true, 'handle')""", acc)
        await conn.execute("""INSERT INTO client_account_roles (client_id, account_id, role, is_active)
                              VALUES ($1::uuid, $2::uuid, 'owned', true)""", a, acc)
        await conn.execute(
            """INSERT INTO harvested_signals (platform, profile_key, content_id, content_type, published_at, likes, account_id)
               VALUES ('instagram', 'klinika', '3916144284900073637', 'image', '2026-10-05', 10, $1::uuid)""", acc)
    url = "/v1/harvest/posts/instagram/3916144284900073637/marks"
    assert (await api(BM).put(url, json={"marks": ["iklan", "tidak_relevan"]})).json()["marks"] == ["iklan", "tidak_relevan"]
    async with hub_db.acquire() as conn:
        assert await conn.fetchval("SELECT advertisement FROM harvested_signals") is True
    assert (await api(BM).put(url, json={"marks": ["tidak_relevan"]})).json()["marks"] == ["tidak_relevan"]
    async with hub_db.acquire() as conn:
        assert await conn.fetchval("SELECT advertisement FROM harvested_signals") is False
        assert await conn.fetchval("SELECT count(*) FROM post_review_marks WHERE cleared_at IS NOT NULL") == 1
    assert (await api(BM).put(url, json={"marks": ["palsu"]})).status_code == 422
    posts = (await api(BM).get(f"/v1/harvest/accounts/{acc}/posts?month=2026-10")).json()["posts"]
    assert posts[0]["marks"] == ["tidak_relevan"] and posts[0]["url"].startswith("https://www.instagram.com/p/")
