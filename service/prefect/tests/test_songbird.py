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

def _make_idea(i):
    return {
        "topik": f"Topik {i}", "bentuk": "Reels", "format": "Video",
        "purpose_theme": "Awareness", "strategic_application": "Funnel top",
        "visualisasi_konten": "Shot list…", "adapted_pattern": "hook cepat",
        "source_exemplar": "@competitor/123", "rationale": "cocok",
    }


@pytest.fixture
def patched_engine(monkeypatch):
    """Patch all external task calls the engine makes; capture delivery calls."""
    calls = {"sheets_created": [], "appended": [], "read_header": None}

    async def fake_context(client):
        return {"client_name": client, "records": [{"subject": "Voice", "information": "santai"}]}

    async def fake_top(handles, limit=8, window_days=180):
        # Non-empty ⇒ signal available; assert window passed through.
        assert window_days == 180
        return [{"profile_key": handles[0], "content_type": "video", "likes": 100,
                 "comments": 10, "content_flow": "hook-body-cta", "summary": "demo"}]

    async def fake_quantity(client, credentials_block_name="google-creds"):
        return 3

    async def fake_openrouter(user, system=None, response_format=None, max_tokens=4000, temperature=0.8):
        n = int(user.split("TEPAT ")[1].split(" ide")[0])
        return {"items": [_make_idea(i) for i in range(n)]}

    async def fake_folder(name, parent, credentials_block_name="google-creds"):
        return "folder-id"

    async def fake_create(title, parent, header_row=None, credentials_block_name="google-creds"):
        calls["sheets_created"].append({"title": title, "header": header_row})
        return "sheet-id"

    async def fake_append(spreadsheet_id, rows, sheet_name="Sheet1", credentials_block_name="google-creds"):
        calls["appended"].append({"id": spreadsheet_id, "rows": rows, "tab": sheet_name})
        return {"updates": {"updatedRows": len(rows)}}

    async def fake_read(spreadsheet_id, sheet_name, credentials_block_name="google-creds", **kw):
        return {"dataframe_info": {"columns": calls["read_header"]}}

    import logging as _logging
    monkeypatch.setattr(engine, "get_run_logger", lambda: _logging.getLogger("test"))
    monkeypatch.setattr(engine, "songbird_client_context", fake_context)
    monkeypatch.setattr(engine, "songbird_top_performers", fake_top)
    monkeypatch.setattr(engine, "songbird_config_quantity", fake_quantity)
    monkeypatch.setattr(engine, "openrouter_chat", fake_openrouter)
    monkeypatch.setattr(engine, "drive_folder_ensure", fake_folder)
    monkeypatch.setattr(engine, "sheets_create", fake_create)
    monkeypatch.setattr(engine, "sheets_rows_append", fake_append)
    monkeypatch.setattr(engine, "google_read_sheet_data", fake_read)
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
    # Draft sheet created with content-plan + rationale header.
    created = patched_engine["sheets_created"][0]
    assert created["header"] == engine.DRAFT_HEADER
    # Every row has Topik / in-month Tanggal / Bentuk.
    rows = patched_engine["appended"][0]["rows"]
    assert len(rows) == 3
    topik_i, tanggal_i, bentuk_i = 0, 1, 2
    for r in rows:
        assert r[topik_i] and r[bentuk_i]
        assert r[tanggal_i].startswith("2026-08-")
    # Hit Note column carries the disclaimer.
    assert rows[0][engine.DRAFT_HEADER.index("Hit Note")] == engine.HIT_DISCLAIMER


@pytest.mark.asyncio
async def test_monthly_quantity_from_config_when_none(patched_engine):
    result = await engine.run_generation(
        client="Acme", quantity=None, distribute_dates=True,
        resolve_quantity_from_config=True, month="Agustus 2026",
    )
    assert result["summary"]["ideas_requested"] == 3  # from fake_quantity
    assert result["summary"]["ideas_produced"] == 3


@pytest.mark.asyncio
async def test_signal_unavailable_still_generates(patched_engine, monkeypatch):
    async def no_signal(handles, limit=8, window_days=180):
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
    async def one_bad(user, system=None, response_format=None, max_tokens=4000, temperature=0.8):
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
    patched_engine["read_header"] = ["Bentuk", "Waktu", "Topik", "Tanggal", "Visualisasi Konten"]
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
