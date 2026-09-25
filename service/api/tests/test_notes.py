"""Old notes → Intakes against real Postgres, model faked (US6, R7, FR-040, FR-041, G-17)."""
import pytest

from app.ai.openrouter import AiFailure, AiResult
from app.card.definition import load_file
from app.intake import notes, pipeline
from tests.conftest import ROOT, add_client, add_person

pytestmark = pytest.mark.schema

ANSWER = {"items": [{"target": "profil", "field_key": "target_audiens", "value": "Pelajar dan mahasiswa",
                     "excerpt": "target pelajar dan mahasiswa", "speaker": None, "spoke_at": None,
                     "valid_until": None, "is_update": False, "is_patient_data": False}],
          "no_card_home": [], "nothing_found": False}


@pytest.fixture
def fake_ai(monkeypatch):
    class Calls(list):
        failures: list

    calls = Calls()
    failures = []  # push AiFailure reasons; each is raised by one call, in order

    async def chat_json(**kwargs):
        calls.append(kwargs["call_site"])
        if failures:
            raise AiFailure(failures.pop(0), "429 upstream")
        return AiResult(ANSWER, "xiaomi/mimo-v2.5", "Xiaomi", 2000, 200, 0.0008, 1)

    monkeypatch.setattr(notes, "PACE_SECONDS", 0)
    monkeypatch.setattr(notes, "PROVIDER_RETRY_WAIT", 0)
    calls.failures = failures

    monkeypatch.setattr(pipeline, "chat_json", chat_json)
    return calls


async def _note(conn, client_name, subject, info, client_id=None):
    return str(await conn.fetchval(
        """INSERT INTO knowledge_records (client_name, client_key, subject, subject_key, information, source_type,
                                          client_id)
           VALUES ($1, lower($1), $2, lower($2), $3, 'plain_text', $4::uuid) RETURNING id""",
        client_name, subject, info, client_id))


async def _setup(hub_db):
    async with hub_db.acquire() as conn:
        sampang = await add_client(conn, "Klinik Mata Sampang", "eskala")
        await add_client(conn, "Klinik Mata Bireuen", "eskala")
        await add_person(conn, "pm@noktah.co", "PM Eskala", "project_manager", "eskala")
        await _note(conn, "Klinik Mata Sampang", "audiens", "Kami target pelajar dan mahasiswa.", sampang)
        await _note(conn, "KM Sampang", "services offered", "We offer LASIK and cataract surgery.")  # no match
        await _note(conn, "Klinik Mata", "kontak", "WA admin 0813-9999-0000")  # ambiguous: two clinics
        await _note(conn, "klinik mata bireuen", "jam", "Buka 08.00-20.00, target pelajar dan mahasiswa")
    return sampang


async def test_dry_run_makes_no_calls_and_projects_spend(hub_db, fake_ai):
    await _setup(hub_db)
    async with hub_db.acquire() as conn:
        report = await notes.process(conn, load_file(str(ROOT / "config" / "hub")), dry_run=True)
        assert await conn.fetchval("SELECT count(*) FROM intakes") == 0
    assert fake_ai == []
    assert report["in_scope"] == 4 and report["matched"] == 2 and report["unmatched"] == 2
    assert report["projected_usd"] == pytest.approx(2 * notes.FALLBACK_COST_PER_NOTE)
    assert report["unmatched_names"] == ["KM Sampang", "Klinik Mata"]


async def test_real_run_is_idempotent_and_never_guesses_a_client(hub_db, fake_ai):
    await _setup(hub_db)
    d = load_file(str(ROOT / "config" / "hub"))
    async with hub_db.acquire() as conn:
        report = await notes.process(conn, d, dry_run=False)
        assert report["ready"] == 2 and report["unmatched"] == 2 and fake_ai == ["hub.notes", "hub.notes"]
        assert report["progress"] == {"processed": 4, "matched": 2, "unmatched": 2, "discarded": 0, "remaining": 0}
        again = await notes.process(conn, d, dry_run=False)
        assert again["in_scope"] == 0 and len(fake_ai) == 2
        # nothing reached a card
        assert await conn.fetchval("SELECT count(*) FROM card_values") == 0
        assert await conn.fetchval("SELECT count(*) FROM intakes WHERE status = 'unmatched' AND client_id IS NULL") == 2


async def test_unmatched_list_assign_and_discard(hub_db, api, fake_ai):
    sampang = await _setup(hub_db)
    async with hub_db.acquire() as conn:
        await notes.process(conn, load_file(str(ROOT / "config" / "hub")), dry_run=False)
    pm = api("pm@noktah.co")
    body = (await pm.get("/v1/notes/unmatched")).json()
    assert body["progress"]["unmatched"] == 2
    by_name = {n["source_name"]: n for n in body["notes"]}
    english = by_name["KM Sampang"]
    assert english["text"] == "We offer LASIK and cataract surgery."
    r = await pm.post(f"/v1/notes/{english['id']}/assign", json={"client_id": sampang})
    assert r.status_code == 200, r.text
    assert r.json()["client"]["id"] == sampang and r.json()["status"] == "ready"
    r = await pm.post(f"/v1/notes/{by_name['Klinik Mata']['id']}/discard")
    assert r.status_code == 200
    assert (await pm.post(f"/v1/notes/{english['id']}/discard")).status_code == 409, "already assigned"
    assert (await pm.get("/v1/notes/unmatched")).json()["notes"] == []


async def test_cap_stops_the_run_and_it_resumes(hub_db, fake_ai):
    await _setup(hub_db)
    d = load_file(str(ROOT / "config" / "hub"))
    async with hub_db.acquire() as conn:
        await conn.execute("INSERT INTO ai_ledger (call_site, model, cost_usd) VALUES ('hub.summary', 'm', 5.0)")
        report = await notes.process(conn, d, dry_run=False)
        assert report["paused_by_cap"] is True and fake_ai == []
        await conn.execute("DELETE FROM ai_ledger")
        report = await notes.process(conn, d, dry_run=False)
        assert report["ready"] == 2 and report["progress"]["remaining"] == 0


async def test_provider_errors_are_retried_and_never_mark_a_note_done(hub_db, fake_ai):
    await _setup(hub_db)
    d = load_file(str(ROOT / "config" / "hub"))
    async with hub_db.acquire() as conn:
        # First matched note: fails, then succeeds on the in-run retry.
        # Second matched note: fails twice, so it stays failed for this run...
        fake_ai.failures.extend(["provider_error", "provider_error", "provider_error"])
        report = await notes.process(conn, d, dry_run=False)
        assert report["failed"] == 1 and report["ready"] == 1
        # ...and the next run picks it up again, reusing its Intake row.
        again = await notes.process(conn, d, dry_run=False)
        assert again["in_scope"] == 1 and again["ready"] == 1
        assert await conn.fetchval("SELECT count(*) FROM intakes WHERE kind = 'old_note' AND client_id IS NOT NULL") == 2
        assert await conn.fetchval("SELECT count(*) FROM intakes WHERE status = 'failed'") == 0
