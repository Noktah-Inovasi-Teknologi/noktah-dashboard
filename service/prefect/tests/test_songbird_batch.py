"""
Tests for the batch content-plan flow (flows/songbird_batch_plan.py).

Covers the `clients` parameter's accepted shapes and the batch's failure isolation:
one bad client must not cost the whole run. `run_generation` is monkeypatched, so
these are plain async unit tests (no Prefect runtime, no network, no DB).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("SONGBIRD_DRIVE_PARENT_ID", "parent-folder-id")
os.environ.setdefault("HARVEST_DB_URL", "postgresql://test:test@localhost/test")

from flows import songbird_batch_plan as batch


# --------------------------------------------------------------------------
# Parameter parsing — the shapes an operator actually types
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Klinik Utama Gresik", ["Klinik Utama Gresik"]),
    ("Klinik Utama Gresik, Klinik Mata Sampang",
     ["Klinik Utama Gresik", "Klinik Mata Sampang"]),
    ("Klinik Utama Gresik,Klinik Mata Sampang,Klinik Utama Sumenep",
     ["Klinik Utama Gresik", "Klinik Mata Sampang", "Klinik Utama Sumenep"]),
    ("  Klinik Utama Gresik ,  Klinik Mata Sampang  ",
     ["Klinik Utama Gresik", "Klinik Mata Sampang"]),
    ("Klinik Utama Gresik; Klinik Mata Sampang",
     ["Klinik Utama Gresik", "Klinik Mata Sampang"]),
    ("Klinik Utama Gresik\nKlinik Mata Sampang",
     ["Klinik Utama Gresik", "Klinik Mata Sampang"]),
    (["Klinik Utama Gresik", "Klinik Mata Sampang"],
     ["Klinik Utama Gresik", "Klinik Mata Sampang"]),
    ("", []),
    ("   ", []),
    (None, []),
    (",,,", []),
])
def test_parse_clients_accepts_single_and_multiple(raw, expected):
    assert batch.parse_clients(raw) == expected


def test_parse_clients_collapses_internal_whitespace():
    assert batch.parse_clients("Klinik   Utama    Gresik") == ["Klinik Utama Gresik"]


def test_parse_clients_deduplicates_case_insensitively():
    """A pasted roster often repeats a name; generating twice would waste a run."""
    assert batch.parse_clients("Gresik, gresik, GRESIK") == ["Gresik"]


# --------------------------------------------------------------------------
# Batch behaviour
# --------------------------------------------------------------------------

@pytest.fixture
def patched_batch(monkeypatch):
    """Capture run_generation calls; `failing` names return an error result."""
    calls = {"generated": [], "failing": set()}

    async def fake_run_generation(client, **kwargs):
        calls["generated"].append({"client": client, **kwargs})
        if client in calls["failing"]:
            return {"data": [], "summary": {"client": client, "ideas_produced": 0}, "error": "no config row"}
        return {
            "data": [],
            "summary": {"client": client, "ideas_produced": 4, "deliverable_id": f"sheet-{client}"},
            "error": None,
        }

    async def fake_get_date(**kwargs):
        return "Agustus 2026"

    import logging
    monkeypatch.setattr(batch, "get_run_logger", lambda: logging.getLogger("test"))
    monkeypatch.setattr(batch, "run_generation", fake_run_generation)
    monkeypatch.setattr(batch, "get_date", fake_get_date)
    return calls


@pytest.mark.asyncio
async def test_single_client_generates_once(patched_batch):
    result = await batch.songbird_batch_plan_flow(clients="Klinik Utama Gresik", month="Agustus 2026")
    assert result["error"] is None
    assert result["summary"]["clients_requested"] == 1
    assert result["summary"]["clients_succeeded"] == 1
    assert [c["client"] for c in patched_batch["generated"]] == ["Klinik Utama Gresik"]


@pytest.mark.asyncio
async def test_multiple_clients_each_generate(patched_batch):
    result = await batch.songbird_batch_plan_flow(
        clients="Klinik Utama Gresik, Klinik Mata Sampang, Klinik Utama Sumenep",
        month="Agustus 2026",
    )
    assert result["summary"]["clients_succeeded"] == 3
    assert result["summary"]["ideas_produced"] == 12
    assert len(result["data"]) == 3
    assert [c["client"] for c in patched_batch["generated"]] == [
        "Klinik Utama Gresik", "Klinik Mata Sampang", "Klinik Utama Sumenep",
    ]


@pytest.mark.asyncio
async def test_one_failing_client_does_not_stop_the_batch(patched_batch):
    patched_batch["failing"] = {"Klinik Mata Sampang"}
    result = await batch.songbird_batch_plan_flow(
        clients="Klinik Utama Gresik, Klinik Mata Sampang, Klinik Utama Sumenep",
        month="Agustus 2026",
    )
    assert result["error"] is None                       # partial success is still success
    assert result["summary"]["clients_succeeded"] == 2
    assert result["summary"]["clients_failed"] == 1
    assert "Klinik Mata Sampang" in result["summary"]["failures"]
    assert len(patched_batch["generated"]) == 3          # kept going after the failure


@pytest.mark.asyncio
async def test_all_clients_failing_marks_the_run_failed(patched_batch):
    patched_batch["failing"] = {"A", "B"}
    result = await batch.songbird_batch_plan_flow(clients="A, B", month="Agustus 2026")
    assert result["error"] is not None
    assert result["summary"]["clients_succeeded"] == 0


@pytest.mark.asyncio
async def test_empty_clients_fails_fast_without_generating(patched_batch):
    result = await batch.songbird_batch_plan_flow(clients="")
    assert result["error"] is not None
    assert patched_batch["generated"] == []


@pytest.mark.asyncio
async def test_month_defaults_to_next_month(patched_batch):
    result = await batch.songbird_batch_plan_flow(clients="Acme")
    assert result["summary"]["month"] == "Agustus 2026"
    assert patched_batch["generated"][0]["month"] == "Agustus 2026"


@pytest.mark.asyncio
async def test_per_client_config_is_used_unless_overridden(patched_batch):
    await batch.songbird_batch_plan_flow(clients="Acme", month="Agustus 2026")
    assert patched_batch["generated"][0]["resolve_quantity_from_config"] is True

    patched_batch["generated"].clear()
    await batch.songbird_batch_plan_flow(clients="Acme", month="Agustus 2026", quantity=5)
    assert patched_batch["generated"][0]["resolve_quantity_from_config"] is False
    assert patched_batch["generated"][0]["quantity"] == 5


@pytest.mark.asyncio
async def test_dates_are_distributed_and_target_passed_through(patched_batch):
    await batch.songbird_batch_plan_flow(clients="Acme", month="Agustus 2026", target="live")
    call = patched_batch["generated"][0]
    assert call["distribute_dates"] is True
    assert call["target"] == "live"


@pytest.mark.asyncio
async def test_one_explicit_live_sheet_is_refused_for_several_clients(patched_batch):
    """It would put every client's rows into one client's plan."""
    result = await batch.songbird_batch_plan_flow(
        clients="Klinik Utama Gresik, Klinik Mata Sampang", month="Agustus 2026",
        target="live", live_spreadsheet_id="someones-plan",
    )
    assert "one client" in result["error"]
    assert patched_batch["generated"] == []


@pytest.mark.asyncio
async def test_explicit_live_sheet_is_allowed_for_one_client(patched_batch):
    result = await batch.songbird_batch_plan_flow(
        clients="Klinik Utama Gresik", month="Agustus 2026", target="live", live_spreadsheet_id="gresik-plan",
    )
    assert result["error"] is None
    assert patched_batch["generated"][0]["live_spreadsheet_id"] == "gresik-plan"
