"""
Tests for the Songbird generation engine (flows/common/songbird.py).

Covers US1 (monthly draft: date distribution, draft header/columns, signal
availability, per-idea failure isolation, disclaimer), US2 (on-demand: N
standalone ideas, no dates, draft-only), and US3 (live: name-aligned append,
rationale columns excluded, no fabricated columns). OpenRouter, Google, and DB
tasks are mocked by monkeypatching the names the engine imports directly, so
these run as plain async unit tests (no Prefect runtime / no network).
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("SONGBIRD_DRIVE_PARENT_ID", "parent-folder-id")
os.environ.setdefault("HARVEST_DB_URL", "postgresql://test:test@localhost/test")

from flows.common import songbird as engine


# --------------------------------------------------------------------------
# Pure unit tests: month parsing + date distribution (FR-004, R7)
# --------------------------------------------------------------------------

def test_parse_month_indonesian_and_english():
    assert engine._parse_month("Agustus 2026") == (2026, 8)
    assert engine._parse_month("August 2026") == (2026, 8)
    assert engine._parse_month("Desember 2025") == (2025, 12)


def test_parse_month_rejects_garbage():
    with pytest.raises(ValueError):
        engine._parse_month("nextmonth")
    with pytest.raises(ValueError):
        engine._parse_month("Foo 2026")


def test_distribute_dates_spread_within_month():
    dates = engine._distribute_dates(2026, 8, 4)
    assert len(dates) == 4
    assert all(d.startswith("2026-08-") for d in dates)
    days = [int(d.split("-")[2]) for d in dates]
    assert days == sorted(days)              # ascending
    assert days[0] >= 1 and days[-1] <= 31   # within August
    assert len(set(days)) > 1                # actually spread, not collapsed


def test_distribute_dates_more_than_days_stays_spread():
    dates = engine._distribute_dates(2026, 2, 60)  # Feb 2026 has 28 days
    assert len(dates) == 60
    days = [int(d.split("-")[2]) for d in dates]
    assert min(days) == 1 and max(days) == 28
    assert len(set(days)) > 1  # spread across the month, not one day


# --------------------------------------------------------------------------
# Engine tests — shared fixtures
# --------------------------------------------------------------------------

def _make_idea(i, topik=None):
    """One model-returned idea. `bentuk` is NOT included — the engine sets it, because
    generation is per content type."""
    return {
        "topik": topik or f"Topik {i}",
        "purpose_theme": "Mengedukasi audiens", "strategic_application": "Awareness",
        "shoot_guide": "Scene 1 (3 detik): …", "visualisasi_konten": "SLIDE 1: Hook …",
        "caption": "Hook kuat di kalimat pertama. CTA. #tag",
        "adapted_pattern": "hook cepat", "source_exemplar": "@competitor/123",
        "rationale": "cocok",
    }


@pytest.fixture
def patched_engine(monkeypatch):
    """Patch all external task calls the engine makes; capture delivery calls."""
    calls = {
        "sheets_created": [], "appended": [], "read_header": None, "prompts": [],
        "folders_ensured": [],
        # Per-test knobs: what the Clients worksheet returns, and what `bentuk`
        # values the model replies with (None ⇒ the default "Reels" for every idea).
        "config_mix": {"Post": 2, "Story": 1, "Short Video": 1},
        "short_by": 0,               # how many the model under-delivers on first try
        "calls_per_bucket": {},
        "topic_counter": 0,          # keeps generated topics unique across buckets
        "sheet_own_handles": [],
        "themes": [{"theme": "Edukasi Mata", "scores": [0.8, 0.7, 0.9]}],
        "draft_folder": "client-content-plan-folder",
        # What the client's Content Plan folder holds (live target lookup).
        "plan_files": [{"id": "plan-sheet-id", "name": "Content Plan - Acme - Agustus 2026",
                        "mimeType": "application/vnd.google-apps.spreadsheet"}],
        "plan_tabs": ["Sheet1"],
        "plan_rows": [],             # rows already in the live plan
        "listed_folders": [],
    }

    async def fake_context(client):
        return {"client_name": client, "records": [{"subject": "Voice", "information": "santai"}]}

    async def fake_top(handles, limit=8, window_days=180, **kwargs):
        # Non-empty ⇒ signal available; assert window passed through.
        assert window_days == 180
        stats = kwargs.get("stats")
        if stats is not None:
            stats.update({"quota": {"Short Video": 1, "Post": 1}, "by_bucket": {"Short Video": 1, "Post": 1},
                          "gaps": [], "coverage": "full", "accounts": 1, "candidates_considered": 4})
        return [
            {"profile_key": handles[0], "content_type": "video", "bucket": "Short Video",
             "likes": 100, "comments": 10, "engagement": 110, "views": 20000,
             "engagement_rate": 0.0055, "age_days": 12.0, "score": 0.81,
             "caption": "Kenapa mata minus bisa nambah terus? #eyecare",
             "content_flow": "hook-body-cta", "summary": "demo"},
            {"profile_key": handles[0], "content_type": "carousel", "bucket": "Post",
             "likes": 40, "comments": 0, "engagement": 40, "views": None,
             "engagement_rate": None, "age_days": 30.0, "score": 0.66,
             "caption": "5 mitos soal kacamata", "content_flow": "slide-by-slide", "summary": "carousel demo"},
        ]

    async def fake_own_handles(client, credentials_block_name="google-creds"):
        return calls["sheet_own_handles"]

    async def fake_themes(performers, max_themes=8, client=None):
        return calls["themes"]

    async def fake_draft_folder(client, credentials_block_name="google-creds"):
        return calls["draft_folder"]

    async def fake_mix(client, credentials_block_name="google-creds"):
        return dict(calls["config_mix"])

    async def fake_openrouter(
        user, system=None, response_format=None, max_tokens=4000, temperature=0.8,
        call_site="unknown", client=None,
    ):
        calls["prompts"].append(user)
        calls.setdefault("call_sites", []).append(call_site)
        calls.setdefault("usage_clients", []).append(client)
        # Regex, not split(): exemplar captions are injected into the prompt *before*
        # this marker, so a caption containing "TEPAT " would break a naive split.
        asked = int(re.search(r"TEPAT (\d+) ide", user).group(1))
        # `short_by` simulates a model that under-delivers, which is what the top-up
        # loop exists to recover from. It applies only to the first call per bucket.
        bucket = re.search(r"berbentuk \*\*(.+?)\*\*", user)
        key = bucket.group(1) if bucket else ""
        calls["calls_per_bucket"][key] = calls["calls_per_bucket"].get(key, 0) + 1
        n = asked
        if calls["short_by"] and calls["calls_per_bucket"][key] == 1:
            n = max(asked - calls["short_by"], 0)
        offset = calls["topic_counter"]
        calls["topic_counter"] += n
        return {"items": [_make_idea(offset + i) for i in range(n)]}

    async def fake_folder(name, parent, credentials_block_name="google-creds"):
        calls["folders_ensured"].append({"name": name, "parent": parent})
        return "folder-id"

    async def fake_create(title, parent, header_row=None, credentials_block_name="google-creds"):
        calls["sheets_created"].append({"title": title, "header": header_row})
        return "sheet-id"

    async def fake_append(spreadsheet_id, rows, sheet_name="Sheet1", credentials_block_name="google-creds"):
        calls["appended"].append({"id": spreadsheet_id, "rows": rows, "tab": sheet_name})
        return {"updates": {"updatedRows": len(rows)}}

    async def fake_read(spreadsheet_id, sheet_name, credentials_block_name="google-creds", **kw):
        return {"dataframe_info": {"columns": calls["read_header"]}, "data": calls["plan_rows"]}

    async def fake_list_folder(folder_id, file_name_pattern=None, credentials_block_name="google-creds", **kw):
        calls["listed_folders"].append(folder_id)
        return list(calls["plan_files"])

    async def fake_info(spreadsheet_id, credentials_block_name="google-creds"):
        return {"sheets": [{"title": t} for t in calls["plan_tabs"]]}

    async def fake_run_record_start(**kwargs):
        calls.setdefault("run_record_start", []).append(kwargs)
        return "run-1"

    async def fake_run_record_finish(**kwargs):
        calls.setdefault("run_record_finish", []).append(kwargs)

    import logging as _logging
    monkeypatch.setattr(engine, "get_run_logger", lambda: _logging.getLogger("test"))
    monkeypatch.setattr(engine, "songbird_client_context", fake_context)
    monkeypatch.setattr(engine, "songbird_top_performers", fake_top)
    monkeypatch.setattr(engine, "songbird_config_content_mix", fake_mix)
    monkeypatch.setattr(engine, "songbird_config_own_handles", fake_own_handles)
    monkeypatch.setattr(engine, "songbird_theme_induction", fake_themes)
    monkeypatch.setattr(engine, "songbird_config_draft_folder", fake_draft_folder)
    monkeypatch.setattr(engine, "songbird_config_plan_folder", fake_draft_folder)
    monkeypatch.setattr(engine, "openrouter_chat", fake_openrouter)
    monkeypatch.setattr(engine, "drive_folder_ensure", fake_folder)
    monkeypatch.setattr(engine, "sheets_create", fake_create)
    monkeypatch.setattr(engine, "sheets_rows_append", fake_append)
    monkeypatch.setattr(engine, "google_read_sheet_data", fake_read)
    monkeypatch.setattr(engine, "google_filter_files_in_folder", fake_list_folder)
    monkeypatch.setattr(engine, "google_read_spreadsheet_info", fake_info)
    monkeypatch.setattr(engine, "run_record_start", fake_run_record_start)
    monkeypatch.setattr(engine, "run_record_finish", fake_run_record_finish)
    monkeypatch.setattr(engine, "CLIENT_SOCIAL", {"Acme": {"own": ["acme"], "competitors": ["rival"]}})
    return calls


# --------------------------------------------------------------------------
# US1 — monthly draft
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_monthly_draft_happy_path(patched_engine):
    result = await engine.run_generation(
        client="Acme", quantity=3, distribute_dates=True, month="Agustus 2026", target="draft",
    )
    assert result["error"] is None
    s = result["summary"]
    assert s["ideas_produced"] == 3 and s["ideas_failed"] == 0
    assert s["signal_available"] is True
    assert s["target"] == "draft"
    assert s["hit_disclaimer"] == engine.HIT_DISCLAIMER
    # Draft sheet matches the v5 content-plan layout exactly.
    created = patched_engine["sheets_created"][0]
    assert created["header"] == engine.DRAFT_HEADER
    rows = patched_engine["appended"][0]["rows"]
    assert len(rows) == 3
    col = {name: i for i, name in enumerate(engine.DRAFT_HEADER)}
    for n, r in enumerate(rows, start=1):
        assert r[col["No."]] == str(n)
        assert r[col["Topik"]] and r[col["Bentuk"]]
        assert r[col["Tanggal"]].startswith("2026-08-")
        assert r[col["Creator"]] == engine.DEFAULT_CREATOR
        assert r[col["Caption"]]
        assert r[col["Format"]] == r[col["Bentuk"]]   # v5 mirrors Bentuk into Format


@pytest.mark.asyncio
async def test_generation_calls_are_attributed_for_cost_accounting(patched_engine):
    """Every OpenRouter call must say which client and code path spent the tokens.

    Unattributed spend is unmeasurable spend — the counts come back on every
    response, so the only thing that can be missing is who to bill them to.
    """
    await engine.run_generation(
        client="Acme", content_mix={"Post": 2}, distribute_dates=True, month="Agustus 2026",
    )
    assert patched_engine["usage_clients"], "no OpenRouter call was made"
    assert set(patched_engine["usage_clients"]) == {"Acme"}
    assert all(cs.startswith("songbird.generate[") for cs in patched_engine["call_sites"])


def test_draft_header_matches_the_v5_content_plan_layout():
    """The draft must be the same sheet reviewers already work in."""
    assert engine.DRAFT_HEADER == [
        "No.", "Tanggal", "Waktu", "Bentuk", "Topik", "Creator", "Format",
        "Purpose/Theme", "Strategic Application", "Kebutuhan Personil", "Known Facts",
        "Shoot Guide", "Visualisasi Konten", "Asset", "Caption", "Keterangan", "Approval",
        "Link Referensi", "TicketID", "Key",
    ]


@pytest.mark.asyncio
async def test_workflow_columns_are_left_blank_for_humans(patched_engine):
    await engine.run_generation(
        client="Acme", quantity=2, distribute_dates=True, month="Agustus 2026",
    )
    col = {name: i for i, name in enumerate(engine.DRAFT_HEADER)}
    row = patched_engine["appended"][0]["rows"][0]
    for name in ("Waktu", "Kebutuhan Personil", "Known Facts", "Asset",
                 "Keterangan", "Approval", "Link Referensi", "TicketID", "Key"):
        assert row[col[name]] == "", f"{name} should be left for the production workflow"


@pytest.mark.asyncio
async def test_monthly_amounts_come_from_the_clients_worksheet(patched_engine):
    """Total and composition both come from config: Post 2 + Story 1 + Short Video 1."""
    result = await engine.run_generation(
        client="Acme", quantity=None, distribute_dates=True,
        resolve_quantity_from_config=True, month="Agustus 2026",
    )
    s = result["summary"]
    assert s["ideas_requested"] == 4
    assert s["ideas_produced"] == 4
    assert s["content_mix_requested"] == {"Post": 2, "Story": 1, "Short Video": 1}
    assert s["content_mix_delivered"] == {"Post": 2, "Story": 1, "Short Video": 1}


@pytest.mark.asyncio
async def test_each_content_type_is_generated_in_its_own_call(patched_engine):
    """Composition can't come out wrong if each call only ever asks for one type."""
    await engine.run_generation(
        client="Acme", quantity=None, distribute_dates=True,
        resolve_quantity_from_config=True, month="Agustus 2026",
    )
    prompts = patched_engine["prompts"]
    assert len(prompts) == 3                       # one per configured content type
    assert "SEMUA ide harus berbentuk **Post**" in prompts[0]
    assert "TEPAT 2 ide" in prompts[0]
    assert "SEMUA ide harus berbentuk **Story**" in prompts[1]
    assert "SEMUA ide harus berbentuk **Short Video**" in prompts[2]


@pytest.mark.asyncio
async def test_prompt_brief_is_tailored_per_content_type(patched_engine):
    await engine.run_generation(
        client="Acme", quantity=None, distribute_dates=True,
        resolve_quantity_from_config=True, month="Agustus 2026",
    )
    post, story, video = patched_engine["prompts"]
    assert "CAROUSEL" in post and "SLIDE 2 HARUS" in post
    assert "shoot_guide: isi tanda '-'" in post     # Posts need no footage plan
    assert "Scene n" in story
    assert "SCENE n" in video and "hook 3 detik" in video


@pytest.mark.asyncio
async def test_shortfall_is_topped_up_until_the_quota_is_met(patched_engine):
    """A client is contracted for an amount; a short model reply must not shrink it."""
    patched_engine["short_by"] = 1                 # under-deliver once per bucket
    result = await engine.run_generation(
        client="Acme", quantity=None, distribute_dates=True,
        resolve_quantity_from_config=True, month="Agustus 2026",
    )
    s = result["summary"]
    assert s["ideas_produced"] == 4
    assert s["ideas_failed"] == 0
    assert s["content_mix_delivered"] == {"Post": 2, "Story": 1, "Short Video": 1}
    # Each bucket needed a second call to make up its deficit.
    assert all(v >= 2 for v in patched_engine["calls_per_bucket"].values())


@pytest.mark.asyncio
async def test_topup_prompt_avoids_repeating_existing_topics(patched_engine):
    patched_engine["short_by"] = 1
    await engine.run_generation(
        client="Acme", quantity=None, distribute_dates=True,
        resolve_quantity_from_config=True, month="Agustus 2026",
    )
    topups = [p for p in patched_engine["prompts"] if "HINDARI mengulang topik" in p]
    assert topups, "a top-up call should tell the model what already exists"


@pytest.mark.asyncio
async def test_duplicate_topics_are_rejected(patched_engine, monkeypatch):
    async def repeating(user, system=None, response_format=None, max_tokens=4000, temperature=0.8):
        patched_engine["prompts"].append(user)
        return {"items": [_make_idea(0, topik="Sama"), _make_idea(1, topik="Sama")]}
    monkeypatch.setattr(engine, "openrouter_chat", repeating)
    result = await engine.run_generation(
        client="Acme", quantity=2, distribute_dates=True, month="Agustus 2026",
    )
    topics = [row["topik"] for row in result["data"]]
    assert len(topics) == len(set(topics))


@pytest.mark.asyncio
async def test_delivered_bentuk_matches_the_configured_types(patched_engine):
    result = await engine.run_generation(
        client="Acme", quantity=None, distribute_dates=True,
        resolve_quantity_from_config=True, month="Agustus 2026",
    )
    bentuks = [row["bentuk"] for row in result["data"]]
    assert sorted(bentuks) == ["Post", "Post", "Short Video", "Story"]
    # Interleaved so the calendar alternates types rather than grouping them.
    assert bentuks[:3] == ["Post", "Story", "Short Video"]


@pytest.mark.asyncio
async def test_zero_amount_types_are_never_generated(patched_engine):
    patched_engine["config_mix"] = {"Post": 3}
    result = await engine.run_generation(
        client="Acme", quantity=None, distribute_dates=True,
        resolve_quantity_from_config=True, month="Agustus 2026",
    )
    assert result["summary"]["ideas_requested"] == 3
    assert {row["bentuk"] for row in result["data"]} == {"Post"}


@pytest.mark.asyncio
async def test_bentuk_is_set_by_the_engine_not_the_model(patched_engine):
    """The model is never asked for `bentuk`, so it can't get it wrong."""
    result = await engine.run_generation(
        client="Acme", quantity=None, distribute_dates=True,
        resolve_quantity_from_config=True, month="Agustus 2026",
    )
    assert sorted(row["bentuk"] for row in result["data"]) == [
        "Post", "Post", "Short Video", "Story",
    ]
    assert "bentuk" not in engine.IDEA_KEYS


@pytest.mark.asyncio
async def test_persistent_shortfall_is_reported_after_retries(patched_engine, monkeypatch):
    """When top-ups genuinely can't fill the quota, say so rather than pretend."""
    async def always_short(user, system=None, response_format=None, max_tokens=4000, temperature=0.8):
        patched_engine["prompts"].append(user)
        return {"items": []}
    monkeypatch.setattr(engine, "openrouter_chat", always_short)
    result = await engine.run_generation(
        client="Acme", quantity=2, distribute_dates=True, month="Agustus 2026",
    )
    assert result["error"] is not None          # nothing usable was produced
    assert result["summary"]["ideas_produced"] == 0


@pytest.mark.asyncio
async def test_summary_records_what_the_model_returned(patched_engine):
    result = await engine.run_generation(
        client="Acme", quantity=None, distribute_dates=True,
        resolve_quantity_from_config=True, month="Agustus 2026",
    )
    assert result["summary"]["ideas_returned_by_model"] == 4


@pytest.mark.asyncio
async def test_explicit_quantity_overrides_the_configured_mix(patched_engine):
    result = await engine.run_generation(
        client="Acme", quantity=3, distribute_dates=True,
        resolve_quantity_from_config=True, month="Agustus 2026",
    )
    s = result["summary"]
    assert s["ideas_requested"] == 3
    assert s["content_mix_requested"] is None
    assert s["content_mix_delivered"] is None


@pytest.mark.asyncio
async def test_explicit_content_mix_overrides_config(patched_engine):
    result = await engine.run_generation(
        client="Acme", content_mix={"Story": 2, "Post": 0}, distribute_dates=True,
        month="Agustus 2026",
    )
    s = result["summary"]
    assert s["ideas_requested"] == 2                       # zero-amount types dropped
    assert s["content_mix_requested"] == {"Story": 2}
    assert {row["bentuk"] for row in result["data"]} == {"Story"}


@pytest.mark.asyncio
async def test_draft_lands_in_the_clients_content_plan_folder(patched_engine):
    """Reviewers look in the client's own folder, not a separate songbird silo."""
    result = await engine.run_generation(
        client="Acme", quantity=3, distribute_dates=True, month="Agustus 2026", target="draft",
    )
    assert result["error"] is None
    ensured = patched_engine["folders_ensured"][-1]
    assert ensured == {"name": "Songbird Drafts", "parent": "client-content-plan-folder"}


@pytest.mark.asyncio
async def test_draft_falls_back_to_the_shared_parent_folder(patched_engine, monkeypatch):
    """Clients with no Content Plan Folder ID still deliver, via the env fallback."""
    patched_engine["draft_folder"] = None
    monkeypatch.setenv("SONGBIRD_DRIVE_PARENT_ID", "shared-parent")
    result = await engine.run_generation(
        client="Acme", quantity=3, distribute_dates=True, month="Agustus 2026", target="draft",
    )
    assert result["error"] is None
    assert patched_engine["folders_ensured"][-1]["parent"] == "shared-parent"


@pytest.mark.asyncio
async def test_draft_without_any_folder_fails_with_a_clear_message(patched_engine, monkeypatch):
    patched_engine["draft_folder"] = None
    monkeypatch.setenv("SONGBIRD_DRIVE_PARENT_ID", "")
    result = await engine.run_generation(
        client="Acme", quantity=3, distribute_dates=True, month="Agustus 2026", target="draft",
    )
    assert "Content Plan Folder ID" in (result["error"] or "")


@pytest.mark.asyncio
async def test_without_a_knowledge_base_it_generates_purely_from_harvested_content(
    patched_engine, monkeypatch
):
    """A client with no KB must still produce a plan, grounded in own + competitor content."""
    async def no_kb(client):
        return {"client_name": client, "records": []}
    monkeypatch.setattr(engine, "songbird_client_context", no_kb)

    result = await engine.run_generation(
        client="Acme", quantity=3, distribute_dates=True, month="Agustus 2026",
    )
    assert result["error"] is None
    assert result["summary"]["ideas_produced"] == 3
    assert result["summary"]["grounding"] == "signal_only"

    prompt = patched_engine["prompts"][0]
    assert "TIDAK TERSEDIA" in prompt
    assert "SATU-SATUNYA acuan brand" in prompt      # harvested content is the grounding
    assert "JANGAN mengarang fakta spesifik" in prompt


@pytest.mark.asyncio
async def test_no_knowledge_base_widens_the_exemplar_budget(patched_engine, monkeypatch):
    """Harvested content is the only grounding left, so the model gets more of it."""

    async def run_with_records(records):
        limits = []

        async def context(client):
            return {"client_name": client, "records": records}

        async def capture_limit(handles, limit=8, window_days=180, **kwargs):
            limits.append(limit)
            return []

        monkeypatch.setattr(engine, "songbird_client_context", context)
        monkeypatch.setattr(engine, "songbird_top_performers", capture_limit)
        await engine.run_generation(
            client="Acme", quantity=4, distribute_dates=True, month="Agustus 2026"
        )
        return max(limits)

    with_kb = await run_with_records([{"subject": "Voice", "information": "santai"}])
    without_kb = await run_with_records([])
    assert without_kb > with_kb


@pytest.mark.asyncio
async def test_grounding_modes_are_reported(patched_engine, monkeypatch):
    result = await engine.run_generation(
        client="Acme", quantity=2, distribute_dates=True, month="Agustus 2026",
    )
    assert result["summary"]["grounding"] == "knowledge_base+signal"

    async def no_signal(handles, limit=8, window_days=180, **kwargs):
        return []
    monkeypatch.setattr(engine, "songbird_top_performers", no_signal)
    result = await engine.run_generation(
        client="Acme", quantity=2, distribute_dates=True, month="Agustus 2026",
    )
    assert result["summary"]["grounding"] == "knowledge_base_only"


@pytest.mark.asyncio
async def test_signal_unavailable_still_generates(patched_engine, monkeypatch):
    async def no_signal(handles, limit=8, window_days=180, **kwargs):
        return []
    monkeypatch.setattr(engine, "songbird_top_performers", no_signal)
    result = await engine.run_generation(
        client="Acme", quantity=3, distribute_dates=True, month="Agustus 2026",
    )
    assert result["error"] is None
    assert result["summary"]["signal_available"] is False
    assert result["summary"]["ideas_produced"] == 3  # degrades gracefully (FR-011)


@pytest.mark.asyncio
async def test_single_malformed_idea_isolated(patched_engine, monkeypatch):
    async def one_bad(
        user, system=None, response_format=None, max_tokens=4000, temperature=0.8,
        call_site="unknown", client=None,
    ):
        items = [_make_idea(0), {"topik": "", "bentuk": ""}, _make_idea(2)]  # middle malformed
        return {"items": items}
    monkeypatch.setattr(engine, "openrouter_chat", one_bad)
    result = await engine.run_generation(
        client="Acme", quantity=3, distribute_dates=True, month="Agustus 2026",
    )
    # Exactly one lost, remaining delivered (SC-007), no raise.
    assert result["error"] is None
    assert result["summary"]["ideas_produced"] == 2
    assert result["summary"]["ideas_failed"] == 1


# --------------------------------------------------------------------------
# US2 — on-demand (draft-only, no dates)
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_on_demand_no_dates_draft_only(patched_engine):
    result = await engine.run_generation(
        client="Acme", quantity=3, distribute_dates=False, target="live",  # target ignored on-demand
    )
    assert result["error"] is None
    assert result["summary"]["target"] == "draft"       # forced draft (FR-015)
    rows = patched_engine["appended"][0]["rows"]
    tanggal_i = engine.DRAFT_HEADER.index("Tanggal")
    assert all(r[tanggal_i] == "" for r in rows)         # no dates assigned
    # A draft sheet was created; the live worksheet was never read.
    assert patched_engine["sheets_created"] and patched_engine["read_header"] is None


# --------------------------------------------------------------------------
# US3 — live handoff (name-aligned append, rationale excluded)
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_live_aligns_by_name_and_drops_rationale(patched_engine):
    # Shuffled live header with an extra unrelated column + no rationale columns.
    patched_engine["read_header"] = ["Bentuk", "Waktu", "Topik", "Tanggal", "Shoot Guide", "Visualisasi Konten"]
    result = await engine.run_generation(
        client="Acme", quantity=3, distribute_dates=True, month="Agustus 2026", target="live",
    )
    assert result["error"] is None
    assert result["summary"]["target"] == "live"
    appended = patched_engine["appended"][0]
    assert appended["tab"] is not None
    rows = appended["rows"]
    header = patched_engine["read_header"]
    # Each row aligns to the live header order; rationale columns never appear.
    assert all(len(r) == len(header) for r in rows)
    assert "Adapted Pattern" not in header  # sanity
    # Topik lands under the Topik column position; Waktu (unmapped) stays blank.
    topik_pos, waktu_pos = header.index("Topik"), header.index("Waktu")
    assert rows[0][topik_pos].startswith("Topik")
    assert rows[0][waktu_pos] == ""


@pytest.mark.asyncio
async def test_live_populates_visualisasi_konten_beside_shoot_guide(patched_engine):
    """
    Every live plan has BOTH "Shoot Guide" and "Visualisasi Konten" (no "Reference").
    The name-aligned append blanks any draft column the live header lacks, so the
    draft must call the column by the live sheet's name or the content silently vanishes.
    """
    patched_engine["read_header"] = [
        "No.", "Tanggal", "Bentuk", "Topik", "Known Facts", "Shoot Guide", "Visualisasi Konten", "Asset",
    ]
    result = await engine.run_generation(
        client="Acme", quantity=2, distribute_dates=True, month="Agustus 2026", target="live",
    )
    assert result["error"] is None
    header = patched_engine["read_header"]
    rows = patched_engine["appended"][0]["rows"]
    sg, vk = header.index("Shoot Guide"), header.index("Visualisasi Konten")
    assert all(r[sg].startswith("Scene 1") for r in rows)
    assert all(r[vk].startswith("SLIDE 1") for r in rows)
    assert "Reference" not in engine.CONTENT_PLAN_COLUMNS


# --------------------------------------------------------------------------
# Live target: the client's own monthly plan, never the Clients roster
# --------------------------------------------------------------------------

LIVE_HEADER = ["No.", "Tanggal", "Waktu", "Bentuk", "Topik", "Shoot Guide", "Visualisasi Konten", "Approval"]


@pytest.mark.asyncio
async def test_live_writes_to_the_clients_own_plan_for_the_month(patched_engine):
    patched_engine["read_header"] = LIVE_HEADER
    result = await engine.run_generation(
        client="Acme", quantity=2, distribute_dates=True, month="August 2026", target="live",
    )
    assert result["error"] is None
    appended = patched_engine["appended"][0]
    # English month input still finds the Indonesian-named file.
    assert appended["id"] == "plan-sheet-id" and appended["tab"] == "Sheet1"
    assert patched_engine["listed_folders"] == ["client-content-plan-folder"]


@pytest.mark.asyncio
async def test_live_refuses_a_worksheet_that_is_not_a_content_plan(patched_engine):
    """The old default target was the Clients roster; its header must be refused."""
    patched_engine["read_header"] = ["Name", "Instagram", "TikTok", "Post", "Story", "Short Video"]
    result = await engine.run_generation(
        client="Acme", quantity=2, distribute_dates=True, month="Agustus 2026", target="live",
    )
    assert "not a content plan" in result["error"]
    assert patched_engine["appended"] == []


@pytest.mark.asyncio
async def test_missing_plan_fails_before_any_model_spend(patched_engine):
    patched_engine["plan_files"] = []
    result = await engine.run_generation(
        client="Acme", quantity=2, distribute_dates=True, month="Agustus 2026", target="live",
    )
    assert "Content Plan - Acme - Agustus 2026" in result["error"]
    assert patched_engine["prompts"] == [], "the plan lookup must run before generation"
    assert patched_engine["appended"] == []


@pytest.mark.asyncio
async def test_a_salinan_copy_is_not_the_plan(patched_engine):
    patched_engine["plan_files"] = [{"id": "copy", "name": "Salinan Content Plan - Acme - Agustus 2026",
                                     "mimeType": "application/vnd.google-apps.spreadsheet"}]
    result = await engine.run_generation(
        client="Acme", quantity=2, distribute_dates=True, month="Agustus 2026", target="live",
    )
    assert result["error"] and patched_engine["appended"] == []


@pytest.mark.asyncio
async def test_live_numbering_continues_after_existing_rows(patched_engine):
    patched_engine["read_header"] = LIVE_HEADER
    patched_engine["plan_rows"] = [{"No.": "1"}, {"No.": "2"}, {"No.": "3"}]
    result = await engine.run_generation(
        client="Acme", quantity=2, distribute_dates=True, month="Agustus 2026", target="live",
    )
    assert result["error"] is None
    rows = patched_engine["appended"][0]["rows"]
    assert [r[LIVE_HEADER.index("No.")] for r in rows] == ["4", "5"]


@pytest.mark.asyncio
async def test_live_uses_the_first_tab_when_there_is_no_sheet1(patched_engine):
    patched_engine["read_header"] = LIVE_HEADER
    patched_engine["plan_tabs"] = ["sheet3", "Arsip"]
    result = await engine.run_generation(
        client="Acme", quantity=2, distribute_dates=True, month="Agustus 2026", target="live",
    )
    assert result["error"] is None
    assert patched_engine["appended"][0]["tab"] == "sheet3"
