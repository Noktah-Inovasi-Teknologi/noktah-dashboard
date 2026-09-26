"""Laporan and Insentif in hub-api (spec 009 US5–US9): the Jira copy, Delivery, Stations,
Performa, points, and sanctions issued after a one-day hold (ADR-0001)."""
from datetime import datetime, timedelta, timezone

import pytest

from app import notify
from app.incentive import sanctions
from app.workdays import WIB
from tests.conftest import add_client, add_person

pytestmark = pytest.mark.schema
OWNER, BM = "core@noktah.co", "defila@noktah.co"
INTERNAL = {"x-hub-internal-token": "tok"}


@pytest.fixture(autouse=True)
def internal_token(monkeypatch):
    monkeypatch.setenv("HUB_API_INTERNAL_TOKEN", "tok")
    from app.settings import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def slack(monkeypatch):
    sent = []

    async def fake(url, text):
        sent.append(text)
        return True
    monkeypatch.setattr(notify, "post_slack", fake)
    return sent


def T(s):
    return s if "T" in s else s + "T03:00:00.000+0000"


def content(key, status, pub, component="10100", changes=(), fa="acc-fa", ce="acc-ce", summary="Konten"):
    return {"key": key, "id": key.split("-")[1], "type": "Content", "status": status,
            "created": "2026-09-01T03:00:00.000+0000", "updated": "2026-09-20T03:00:00.000+0000",
            "fields": {"Summary": summary, "Publication date": pub, "Components": [{"id": component, "name": "X"}],
                       "Field Associate": {"account_id": fa, "name": "Sofi"},
                       "Content Editor": {"account_id": ce, "name": "Nadya"}},
            "links": [],
            "changes": [c if isinstance(c, dict) else
                        {"history_id": f"{key}-{i}", "at": T(c[0]), "author": "a", "field": c[1], "from": c[2], "to": c[3]}
                        for i, c in enumerate(changes)]}


def status_path(key, *steps):
    """steps: (at, from, to[, defect, reason]) → changelog items (a Return carries its Defect Category)."""
    out = []
    for i, s in enumerate(steps):
        out.append({"history_id": f"{key}-h{i}", "at": T(s[0]), "author": "a", "field": "status", "from": s[1], "to": s[2]})
        if len(s) > 3:
            out.append({"history_id": f"{key}-h{i}", "at": T(s[0]), "author": "a", "field": "Defect Category",
                        "from": None, "to": f"Parent values: {s[3][0]} - X(1)Level 1 values: {s[3]} - Y(2)"})
            out.append({"history_id": f"{key}-h{i}", "at": T(s[0]), "author": "a", "field": "Return Reason",
                        "from": None, "to": s[4]})
    return out


def event(key, created, judged_at, person="acc-ce", status="Judged", **fields):
    f = {"Event Type": "Violation", "Violation Judgment": "Kelalaian", "Event Observer": ["External (Client)"],
         "Reporter's Own Mistake/Error": "No", "Problem/Error Solved": "Yes", "Known by the Person": "No",
         "Violation Category": {"parent": "W - Process & Punctuality", "child": "W3 - Konten diserahkan tanpa self-check"},
         "Person": {"account_id": person, "name": "Nadya"}, "Summary": "Salah logo"}
    f.update(fields)
    changes = [{"history_id": f"{key}-j", "at": T(judged_at), "author": "m", "field": "status",
                "from": "Reported", "to": "Judged"}] if judged_at else []
    return {"key": key, "id": key, "type": "Event", "status": status, "created": T(created), "updated": T(created),
            "fields": f, "links": [], "changes": changes}


async def _seed(conn):
    cid = await add_client(conn, "Pelita Delapan", "eskala")
    await conn.execute("UPDATE clients SET jira_component_id = '10100' WHERE id = $1::uuid", cid)
    fa = await add_person(conn, "fa@x.co", "Sofi", "field_associate", "eskala")
    ce = await add_person(conn, "ce@x.co", "Nadya", "content_editor", "eskala")
    qa = await add_person(conn, "qa@x.co", "Juliana", "quality_assurance", "eskala")
    await conn.execute("UPDATE people SET jira_account_id = 'acc-fa' WHERE id = $1::uuid", fa)
    await conn.execute("UPDATE people SET jira_account_id = 'acc-ce' WHERE id = $1::uuid", ce)
    for role, pid in (("field_associate", fa), ("content_editor", ce), ("quality_assurance", qa)):
        await conn.execute(
            """INSERT INTO client_team_assignments (client_id, team_role, person_id, valid_from)
               VALUES ($1::uuid, $2, $3::uuid, '2026-01-01')""", cid, role, pid)
    return cid, fa, ce


async def _ingest(api, issues, done=True):
    r = await api(OWNER).post("/internal/jira/ingest", headers=INTERNAL,
                              json={"issues": issues, "done": done, "started_at": "2026-10-10T00:00:00+00:00"})
    assert r.status_code == 200, r.text
    return r.json()


async def test_ingest_is_idempotent_and_moves_the_cursor(hub_db, api):
    state = (await api(OWNER).post("/internal/jira/sync-state", headers=INTERNAL, json={})).json()
    assert state["first_run"] and state["cursor"].startswith("2026-01-01")
    issue = content("ESKL-1", "Done", "2026-09-05", changes=[("2026-09-02", "status", "Plan", "Footages in Progress")])
    assert (await _ingest(api, [issue]))["new_changes"] == 1
    assert (await _ingest(api, [issue]))["new_changes"] == 0, "a page read twice adds nothing"
    state = (await api(OWNER).post("/internal/jira/sync-state", headers=INTERNAL, json={})).json()
    assert not state["first_run"] and state["cursor"].startswith("2026-10-09T23:55")


async def test_delivery_counts_published_late_and_cancelled(hub_db, api):
    async with hub_db.acquire() as conn:
        await _seed(conn)
    issues = [
        content("ESKL-1", "Done", "2026-09-05", changes=status_path("x", ("2026-09-05T02:00", "Scheduled", "Done"))),
        content("ESKL-2", "Published, Need Review", "2026-09-05",
                changes=status_path("y", ("2026-09-08T02:00", "Scheduled", "Published, Need Review"))),
        content("ESKL-3", "Footages in Progress", "2026-09-05"),
        content("ESKL-4", "Shelved", "2026-09-05"),
        content("ESKL-5", "Plan", "2026-09-30"),
        content("ESKL-6", "On Hold", "2026-09-05"),
    ]
    await _ingest(api, issues)
    from app.reports import delivery
    async with hub_db.acquire() as conn:
        out = await delivery.report(conn, datetime(2026, 9, 1).date(), now=datetime(2026, 9, 26, tzinfo=timezone.utc))
    [row] = [c for c in out["clients"] if c["client"]["name"] == "Pelita Delapan"]
    assert (row["created"], row["published"], row["published_late"], row["late"], row["cancelled"]) == (6, 2, 1, 1, 1)
    assert row["on_hold"] == 1, "On Hold is a manager's pause, never Late (G-52)"
    assert row["late_items"][0]["key"] == "ESKL-3" and row["late_items"][0]["station"] == "B"
    assert row["late_items"][0]["days_late"] == 21
    r = await api(BM).get("/v1/reports/delivery?month=2026-09")
    assert r.status_code == 200 and r.json()["refreshed_at"]


async def test_stations_returns_by_origin_rounds_and_thresholds(hub_db, api):
    async with hub_db.acquire() as conn:
        await _seed(conn)
    steps = [("2026-09-02", "Plan", "Footages in Progress"), ("2026-09-03", "Footages in Progress", "Footage Taken"),
             ("2026-09-04", "Footage Taken", "Designs in Progress"), ("2026-09-05", "Designs in Progress", "Designs in Review"),
             ("2026-09-06", "Designs in Review", "Designs in Progress", "C2", "Logo lama"),
             ("2026-09-07", "Designs in Progress", "Designs in Review"),
             ("2026-09-08", "Designs in Review", "Internally Reviewed"),
             ("2026-09-09", "Internally Reviewed", "In Review by Client"),
             ("2026-09-10", "In Review by Client", "Reviewed by Client"),
             ("2026-09-11", "Reviewed by Client", "Scheduled")]
    old_return = [("2026-09-02", "Plan", "Designs in Review"), ("2026-09-03", "Designs in Review", "Designs in Progress")]
    bouncing = [("2026-09-02", "Plan", "Designs in Progress")]
    for i in range(4):
        bouncing += [(f"2026-09-{3 + 2 * i:02d}", "Designs in Progress", "Designs in Review"),
                     (f"2026-09-{4 + 2 * i:02d}", "Designs in Review", "Designs in Progress", "C4", "Rasio salah")]
    await _ingest(api, [content("ESKL-1", "Scheduled", "2026-09-20", changes=status_path("ESKL-1", *steps)),
                        content("ESKL-2", "Designs in Progress", "2026-09-21", changes=status_path("ESKL-2", *old_return)),
                        content("ESKL-3", "Designs in Progress", "2026-09-22", changes=status_path("ESKL-3", *bouncing))])
    out = (await api(BM).get("/v1/reports/stations?month=2026-09")).json()
    st = {s["station"]: s for s in out["stations"]}
    assert st["C"]["returns"]["by_origin"] == {"C": 5}, "returns with a Defect Category count against its station"
    assert st["D"]["returns"]["by_origin"] == {"tanpa_kategori": 1}, "an old return sits under the sender, uncharged"
    assert st["C"]["in"] == 3 and len(st["C"]["waiting"]) == 2
    [flag] = out["round_flags"]
    assert flag["key"] == "ESKL-3" and flag["rounds"] == 4 and flag["stations"] == ["C", "D"]
    [team] = [t for t in out["teams"] if t["client"]["name"] == "Pelita Delapan"]
    assert team["first_pass"]["counted"] == 1 and team["first_pass"]["rate"] == 1.0
    assert team["qa_rounds"]["counted"] == 3 and team["qa_rounds"]["average"] == pytest.approx((2 + 1 + 4) / 3, 0.01)
    people = {(p["person"]["name"], p["station"]) for p in out["people"]}
    assert ("Nadya", "C") in people and ("Juliana", "D") in people, "C from the issue, D from the Client's Team"
    nadya = next(p for p in out["people"] if p["person"]["name"] == "Nadya")
    assert nadya["returns_against"] == 5


async def test_event_points_are_commented_once_and_reported(hub_db, api):
    async with hub_db.acquire() as conn:
        await _seed(conn)
    out = await _ingest(api, [event("ESKL-20", "2026-10-03", "2026-10-04"),
                              event("ESKL-21", "2026-10-03", "2026-10-04", **{"Violation Judgment": None}),
                              event("ESKL-22", "2026-09-20", "2026-09-21")])
    by = {c["issue_key"]: c for c in out["comments"]}
    assert set(by) == {"ESKL-20"}, "no comment for an incomplete Event, nor one judged before v2.1 counts"
    assert by["ESKL-20"]["text"].startswith("Poin: -30 (Klien × ditemukan orang lain)")
    await api(OWNER).post("/internal/jira/comments", headers=INTERNAL, json={"results": [
        {"issue_key": k, "judged_at": c["judged_at"], "ok": True} for k, c in by.items()]})
    assert (await _ingest(api, [event("ESKL-20", "2026-10-03", "2026-10-04")]))["comments"] == []
    rep = (await api(BM).get("/v1/reports/incentive?month=2026-10")).json()
    nadya = next(p for p in rep["people"] if p["person"]["name"] == "Nadya")
    assert nadya["violation_points"] == -30 and nadya["excellence_points"] == 0
    assert [i["key"] for i in rep["incomplete"]] == ["ESKL-21"]


async def test_sanctions_wait_for_working_day_2_and_issue_after_day_3(hub_db, api, slack):
    async with hub_db.acquire() as conn:
        _cid, _fa, ce = await _seed(conn)
    await _ingest(api, [event("ESKL-30", "2026-10-03", "2026-10-04")])
    wd1 = datetime(2026, 11, 2, 10, tzinfo=WIB)   # Monday = working day 1
    wd2 = datetime(2026, 11, 3, 9, tzinfo=WIB)
    async with hub_db.acquire() as conn:
        assert (await sanctions.tick(conn, wd1))["computed"] == 0 and slack == []
        out = await sanctions.tick(conn, wd2)
        assert out["computed"] == 1 and out["issued"] == 0 and len(slack) == 1
        assert (await sanctions.tick(conn, wd2 + timedelta(hours=1)))["computed"] == 0, "worked out once"
        [s] = await conn.fetch("SELECT * FROM sanctions")
        assert (s["level"], s["state"], s["points"]) == ("sp1", "computed", -30)
        # working day 3 ends at 00:00 on the 5th
        still = await sanctions.tick(conn, datetime(2026, 11, 4, 23, tzinfo=WIB))
        assert still["issued"] == 0
        done = await sanctions.tick(conn, datetime(2026, 11, 5, 0, 30, tzinfo=WIB))
        assert done["issued"] == 1
        [s] = await conn.fetch("SELECT * FROM sanctions")
        assert s["state"] == "issued" and s["letter_number"] == "001/SP/ESK/XI/2026"
        assert str(s["valid_until"]) == "2027-02-03"
        [letter] = done["letters"]
        assert "SURAT PERINGATAN PERTAMA" in letter["html"] and "Nadya" in letter["html"]
        assert "CEO" in letter["html"] and "Brand Manager Eskala" in letter["html"]


async def test_a_manager_can_hold_a_sanction_within_the_window(hub_db, api, slack):
    async with hub_db.acquire() as conn:
        await _seed(conn)
    await _ingest(api, [event("ESKL-31", "2026-10-03", "2026-10-04")])
    async with hub_db.acquire() as conn:
        await sanctions.tick(conn, datetime(2026, 11, 3, 9, tzinfo=WIB))
        sid = await conn.fetchval("SELECT id::text FROM sanctions")
    r = await api(BM).post(f"/v1/sanctions/{sid}/hold", json={"reason": " "})
    assert r.status_code == 422
    r = await api(BM).post(f"/v1/sanctions/{sid}/hold", json={"reason": "Menunggu klarifikasi klien"})
    assert r.status_code == 200 and r.json()["state"] == "held"
    async with hub_db.acquire() as conn:
        assert (await sanctions.tick(conn, datetime(2026, 11, 6, 9, tzinfo=WIB)))["issued"] == 0


async def test_termination_is_only_flagged_and_appeals_hold_a_sanction(hub_db, api, slack):
    async with hub_db.acquire() as conn:
        _c, _f, ce = await _seed(conn)
        await conn.execute("""INSERT INTO sanctions (person_id, level, source, state, issued_on, valid_until)
                              VALUES ($1::uuid, 'sp3', 'recorded', 'issued', '2026-10-01', '2026-12-30')""", ce)
    await _ingest(api, [event("ESKL-40", "2026-10-06", "2026-10-07")])
    async with hub_db.acquire() as conn:
        await sanctions.tick(conn, datetime(2026, 11, 3, 9, tzinfo=WIB))
        s = await conn.fetchrow("SELECT * FROM sanctions WHERE source = 'ladder'")
        assert (s["level"], s["state"]) == ("phk_flag", "flagged")
        await sanctions.tick(conn, datetime(2026, 11, 20, tzinfo=WIB))
        assert await conn.fetchval("SELECT state FROM sanctions WHERE source = 'ladder'") == "flagged"
    # a direct sanction from a judged H2 Event, suspended when the Event is appealed
    h2 = {"Direct Sanction Violation": "4.3.1-5 Mengubah tiket orang lain",
          "Violation Category": {"parent": "H - Attitude", "child": "H2 - Pelanggaran dengan sanksi langsung"}}
    await _ingest(api, [event("ESKL-41", "2026-11-10", "2026-11-10", person="acc-fa", **h2)])
    async with hub_db.acquire() as conn:
        await sanctions.tick(conn, datetime(2026, 11, 10, 12, tzinfo=WIB))
        d = await conn.fetchrow("SELECT * FROM sanctions WHERE source = 'direct'")
        assert (d["level"], d["state"]) == ("sp1", "computed")
    await _ingest(api, [event("ESKL-41", "2026-11-10", "2026-11-10", person="acc-fa", status="Appealed", **h2)])
    async with hub_db.acquire() as conn:
        await sanctions.tick(conn, datetime(2026, 11, 20, 12, tzinfo=WIB))
        assert await conn.fetchval("SELECT state FROM sanctions WHERE source = 'direct'") == "on_appeal"


async def test_recorded_warnings_count_on_the_ladder(hub_db, api):
    async with hub_db.acquire() as conn:
        _c, fa, _ce = await _seed(conn)
    r = await api(BM).post("/v1/sanctions", json={"person_id": fa, "level": "sp1", "issued_on": "2026-10-01",
                                                  "category_code": "w7"})
    assert r.status_code == 200 and r.json()["state"] == "issued" and r.json()["valid_until"] == "2026-12-30"
    assert (await api("ce@x.co").get("/v1/sanctions")).status_code == 403


async def test_performance_leaves_marked_posts_out_and_says_what_it_rests_on(hub_db, api):
    async with hub_db.acquire() as conn:
        cid = await add_client(conn, "Klinik A", "eskala")
        accs = {}
        for handle, role in (("klinika", "owned"), ("komp1", "competitor"), ("komp2", "competitor")):
            acc = await conn.fetchval("INSERT INTO accounts (platform) VALUES ('instagram') RETURNING id::text")
            await conn.execute("""INSERT INTO account_handles (account_id, platform, handle_key, handle_text, is_current,
                                  identifier_kind) VALUES ($1::uuid, 'instagram', $2, $2, true, 'handle')""", acc, handle)
            await conn.execute("""INSERT INTO client_account_roles (client_id, account_id, role, is_active)
                                  VALUES ($1::uuid, $2::uuid, $3, true)""", cid, acc, role)
            accs[handle] = acc
        posts = [("klinika", "1", "video", 1000, 50, 10), ("klinika", "2", "video", 3000, 150, 30),
                 ("klinika", "3", "image", None, 20, None), ("klinika", "4", "video", 90000, 900, 90),
                 ("komp1", "5", "video", 500, 10, 0), ("komp2", "6", "video", 1500, 30, 0),
                 ("klinika", "7", "video", 700, 7, 0)]
        for handle, cid_, kind, views, likes, comments in posts:
            month = datetime(2026, 8, 20, tzinfo=timezone.utc) if cid_ == "7" else datetime(2026, 9, 10, tzinfo=timezone.utc)
            await conn.execute(
                """INSERT INTO harvested_signals (platform, profile_key, content_id, content_type, published_at, views,
                                                  likes, comments, account_id)
                   VALUES ('instagram', $1, $2, $3, $4, $5, $6, $7, $8::uuid)""",
                handle, cid_, kind, month, views, likes, comments, accs[handle])
        await conn.execute("INSERT INTO post_review_marks (platform, content_id, mark) VALUES ('instagram', '4', 'iklan')")
    out = (await api(BM).get(f"/v1/reports/performance?month=2026-09&client_id={cid}")).json()
    own = out["own"]
    assert own["posts"] == 4 and own["posts_counted"] == 3, "a marked post counts as a post, not in the numbers"
    assert own["views"] == {"total": 4000, "average": 2000.0, "n": 2}
    assert [p["content_id"] for p in own["top_posts"]] == ["2", "1", "3"] and own["marked"][0]["content_id"] == "4"
    assert own["engagement_rate_followers"]["value"] is None
    assert "tidak tersedia" in own["engagement_rate_followers"]["unavailable"]
    assert own["change"]["views_total"] == pytest.approx((4000 - 700) / 700, 0.001)
    comp = out["competitors"]
    assert comp["n_accounts"] == 2 and comp["average"]["views"]["total"] == 1000.0
    listing = (await api(BM).get("/v1/reports/performance?month=2026-09")).json()
    assert any(c["client"]["name"] == "Klinik A" and c["posts"] == 4 for c in listing["clients"])
