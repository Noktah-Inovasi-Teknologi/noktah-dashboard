"""
Tests for the shared social-harvest engine (flows/common/social_harvest.py)
and its pacing/rate-limiting utilities.

Covers US1 (happy path: one profile -> folder + Sheet rows with metadata +
analysis) and US2 (100/rolling-hour cap, randomized-delay omission on the
last item, rate-limit/challenge back-off + profile deferral, cross-run
dedupe skip, and continue-on-item/profile-failure). roach and Google Drive
calls are mocked; Prefect task machinery is bypassed by monkeypatching the
names the engine imports directly, so these run as plain async unit tests.
"""
import asyncio
import logging
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("HARVEST_DRIVE_PARENT_ID", "parent-folder-id")
os.environ.setdefault("ROACH_API_URL", "http://roach:8080")
os.environ.setdefault("ROACH_API_KEY", "test-key")
os.environ.setdefault("HARVEST_DB_URL", "postgresql://test:test@localhost/test")

from flows.common import social_harvest as engine
from tasks.social_tasks import RoachNotFoundError, RoachRateLimitedError
from tasks.utility_tasks import RateWindow, randomized_item_delay


# --------------------------------------------------------------------------
# Pure unit tests: rate window + randomized delay
# --------------------------------------------------------------------------

def test_rate_window_allows_up_to_limit_then_blocks():
    window = RateWindow(limit=3, window_seconds=3600.0)
    now = 1_000_000.0
    for _ in range(3):
        assert window.seconds_until_slot(now) == 0.0
        window.record(now)
    assert window.seconds_until_slot(now) > 0.0


def test_rate_window_evicts_expired_entries():
    window = RateWindow(limit=1, window_seconds=10.0)
    window.record(now=1000.0)
    assert window.seconds_until_slot(now=1000.0) > 0.0
    # 11s later, the one entry has aged out of the 10s window
    assert window.seconds_until_slot(now=1011.0) == 0.0


@pytest.mark.asyncio
async def test_randomized_delay_applied_between_items():
    delay = await randomized_item_delay(is_last=False, min_seconds=0.01, max_seconds=0.02)
    assert 0.01 <= delay <= 0.02


@pytest.mark.asyncio
async def test_randomized_delay_omitted_on_last_item():
    start = time.monotonic()
    delay = await randomized_item_delay(is_last=True, min_seconds=5.0, max_seconds=10.0)
    elapsed = time.monotonic() - start
    assert delay == 0.0
    assert elapsed < 0.5  # no sleep actually occurred


# --------------------------------------------------------------------------
# Engine tests
# --------------------------------------------------------------------------

def _item(content_id="1", content_type="video", is_video=True, published_at="2026-07-10T00:00:00Z"):
    return {
        "content_id": content_id, "content_type": content_type, "is_video": is_video,
        "source_url": f"https://x/{content_id}", "published_at": published_at,
        "caption": "hi", "hashtags": [], "public_counts": {"likes": 1, "comments": 0, "views": 5},
    }


@pytest.fixture
def patched_engine(monkeypatch):
    """Bypass Prefect task machinery + get_run_logger for plain async unit testing."""
    monkeypatch.setattr(engine, "get_run_logger", lambda: logging.getLogger("test"))
    calls = {
        "download": [], "analyze": [], "upload": [], "account_rows": [], "detail_rows": [],
        "dedup_check": [], "dedup_record": [], "dedup_delete": [],
        "deleted_files": [], "deleted_rows": [],
    }

    async def fake_sheets_create(title, parent_id, header_row=None, credentials_block_name=None):
        return "detail-sheet"  # per-run detail workbook

    async def fake_drive_folder_ensure(name, parent_id, credentials_block_name=None):
        return f"folder-{name}"

    async def fake_spreadsheet_ensure(title, parent_id, credentials_block_name=None):
        return "acct-sheet"  # per-account workbook

    async def fake_tab_ensure(spreadsheet_id, tab_name, header_row, credentials_block_name=None):
        return None

    async def fake_tab_row_count(spreadsheet_id, tab_name, credentials_block_name=None):
        return 0

    async def fake_drive_file_upload(local_path, folder_id, credentials_block_name=None):
        calls["upload"].append((local_path, folder_id))
        return f"file-{os.path.basename(local_path)}"

    async def fake_sheets_rows_append(spreadsheet_id, rows, sheet_name="Sheet1", credentials_block_name=None):
        bucket = "account_rows" if spreadsheet_id == "acct-sheet" else "detail_rows"
        calls[bucket].extend(rows)
        return {"updates": {"updatedRows": len(rows)}}

    async def fake_rows_delete(spreadsheet_id, content_id, content_id_col, credentials_block_name=None):
        calls["deleted_rows"].append((spreadsheet_id, content_id))
        return 1

    async def fake_file_delete(file_id, credentials_block_name=None):
        calls["deleted_files"].append(file_id)

    async def fake_dedup_check(platform, content_id, drive_target):
        calls["dedup_check"].append((platform, content_id, drive_target))
        return None  # not previously harvested by default

    async def fake_dedup_record(**kwargs):
        calls["dedup_record"].append(kwargs)

    async def fake_dedup_delete(platform, content_id, drive_target):
        calls["dedup_delete"].append((platform, content_id, drive_target))

    async def fake_download(content_id, source_url, is_video, content_type):
        calls["download"].append(content_id)
        return {"local_paths": [f"/data/{content_id}.mp4"], "content_type": content_type}

    async def fake_analyze(content_id, local_paths, content_type):
        calls["analyze"].append(content_id)
        return {"subtitle": "s", "flow": "f", "summary": "sum", "status": "success", "error": None}

    monkeypatch.setattr(engine, "sheets_create", fake_sheets_create)
    monkeypatch.setattr(engine, "drive_folder_ensure", fake_drive_folder_ensure)
    monkeypatch.setattr(engine, "sheets_spreadsheet_ensure", fake_spreadsheet_ensure)
    monkeypatch.setattr(engine, "sheets_tab_ensure", fake_tab_ensure)
    monkeypatch.setattr(engine, "sheets_tab_row_count", fake_tab_row_count)
    monkeypatch.setattr(engine, "drive_file_upload", fake_drive_file_upload)
    monkeypatch.setattr(engine, "sheets_rows_append", fake_sheets_rows_append)
    monkeypatch.setattr(engine, "sheets_rows_delete_by_content_id", fake_rows_delete)
    monkeypatch.setattr(engine, "drive_file_delete", fake_file_delete)
    monkeypatch.setattr(engine, "social_dedup_check", fake_dedup_check)
    monkeypatch.setattr(engine, "social_dedup_record", fake_dedup_record)
    monkeypatch.setattr(engine, "social_dedup_delete", fake_dedup_delete)
    monkeypatch.setattr(engine, "social_item_download", fake_download)
    monkeypatch.setattr(engine, "social_item_analyze", fake_analyze)

    async def fake_delay(is_last, *a, **kw):
        return 0.0

    monkeypatch.setattr(engine, "randomized_item_delay", fake_delay)

    # Avoid real file deletion attempts on fake paths
    monkeypatch.setattr(engine.Path, "unlink", lambda self, missing_ok=True: None)

    return calls


@pytest.mark.asyncio
async def test_happy_path_single_profile_delivers_and_appends_row(patched_engine, monkeypatch):
    async def fake_list(profile_url, platform, max_items=30, stories_only=False):
        return {"profile": {"handle": "acct"}, "items": [_item("1")]}

    monkeypatch.setattr(engine, "social_profile_list", fake_list)

    result = await engine.run_harvest(
        profiles=["https://www.tiktok.com/@acct"],
        depth_selector=engine.make_recent_n_selector(10),
        harvest_name="test",
    )

    assert result["error"] is None
    assert result["summary"]["items_collected"] == 1
    assert result["summary"]["profiles_processed"] == 1
    assert result["summary"]["account_folder_ids"]["acct"] == "folder-acct"
    assert result["summary"]["detail_sheet_id"] == "detail-sheet"
    # one row in the per-account quarterly sheet and one in the per-run detail sheet
    assert len(patched_engine["account_rows"]) == 1
    assert len(patched_engine["detail_rows"]) == 1
    row = patched_engine["account_rows"][0]
    assert row[0] == 1        # id (sequential)
    assert row[1] == "acct"   # username
    assert row[2] == "tiktok" # platform
    assert row[3] == "1"      # content_id
    assert row[13] == "s"     # subtitle
    assert row[14] == "f"     # content_flow
    assert row[15] == "sum"   # summary
    assert row[16] == "test"  # harvest_name
    # detail row carries the account folder id as the extra trailing column
    assert patched_engine["detail_rows"][0][-1] == "folder-acct"


@pytest.mark.asyncio
async def test_more_than_five_profiles_truncated(patched_engine, monkeypatch):
    seen_profiles = []

    async def fake_list(profile_url, platform, max_items=30, stories_only=False):
        seen_profiles.append(profile_url)
        return {"profile": {"handle": profile_url.split("@")[-1]}, "items": []}

    monkeypatch.setattr(engine, "social_profile_list", fake_list)

    profiles = [f"https://www.tiktok.com/@p{i}" for i in range(7)]
    result = await engine.run_harvest(profiles, engine.make_recent_n_selector(10), harvest_name="test")

    assert len(seen_profiles) == 5  # FR-002: truncated to max 5
    assert result["summary"]["profiles_processed"] == 5


@pytest.mark.asyncio
async def test_profile_not_found_is_skipped_and_run_continues(patched_engine, monkeypatch):
    async def fake_list(profile_url, platform, max_items=30, stories_only=False):
        if "bad" in profile_url:
            raise RoachNotFoundError("private account")
        return {"profile": {"handle": "good"}, "items": [_item("1")]}

    monkeypatch.setattr(engine, "social_profile_list", fake_list)

    result = await engine.run_harvest(
        ["https://www.tiktok.com/@bad", "https://www.tiktok.com/@good"],
        engine.make_recent_n_selector(10), harvest_name="test",
    )

    assert result["error"] is None
    assert result["summary"]["profiles_processed"] == 1
    assert result["summary"]["items_collected"] == 1


@pytest.mark.asyncio
async def test_rate_limited_download_backs_off_defers_profile_retains_prior_items(patched_engine, monkeypatch):
    async def fake_list(profile_url, platform, max_items=30, stories_only=False):
        return {"profile": {"handle": "acct"}, "items": [_item("1"), _item("2"), _item("3")]}

    call_count = {"n": 0}

    async def fake_download(content_id, source_url, is_video, content_type):
        if content_id == "1":
            return {"local_paths": ["/data/1.mp4"], "content_type": content_type}
        raise RoachRateLimitedError("challenge", code="challenge")

    async def instant_sleep(seconds):
        return None

    monkeypatch.setattr(engine, "social_profile_list", fake_list)
    monkeypatch.setattr(engine, "social_item_download", fake_download)
    monkeypatch.setattr(asyncio, "sleep", instant_sleep)

    result = await engine.run_harvest(
        ["https://www.tiktok.com/@acct"], engine.make_recent_n_selector(10), harvest_name="test",
    )

    assert result["error"] is None
    # item 1 collected before the block; items 2/3 deferred, not lost/errored as failures
    assert result["summary"]["items_collected"] == 1
    assert result["summary"]["profiles_blocked"] == 1
    assert len(patched_engine["account_rows"]) == 1


@pytest.mark.asyncio
async def test_dedup_skip_already_harvested_success(patched_engine, monkeypatch):
    async def fake_list(profile_url, platform, max_items=30, stories_only=False):
        return {"profile": {"handle": "acct"}, "items": [_item("1")]}

    async def fake_dedup_check(platform, content_id, drive_target):
        return {"analysis_status": "success", "drive_file_id": "old"}  # already harvested OK

    monkeypatch.setattr(engine, "social_profile_list", fake_list)
    monkeypatch.setattr(engine, "social_dedup_check", fake_dedup_check)

    result = await engine.run_harvest(
        ["https://www.tiktok.com/@acct"], engine.make_recent_n_selector(10), harvest_name="test",
    )

    assert result["summary"]["items_skipped_dedup"] == 1
    assert result["summary"]["items_collected"] == 0
    assert patched_engine["download"] == []  # never re-downloaded (FR-021)


@pytest.mark.asyncio
async def test_prior_failed_item_is_purged_and_reharvested(patched_engine, monkeypatch):
    async def fake_list(profile_url, platform, max_items=30, stories_only=False):
        return {"profile": {"handle": "acct"}, "items": [_item("1")]}

    async def fake_dedup_check(platform, content_id, drive_target):
        return {"analysis_status": "failed", "drive_file_id": "oldfile1,oldfile2"}

    monkeypatch.setattr(engine, "social_profile_list", fake_list)
    monkeypatch.setattr(engine, "social_dedup_check", fake_dedup_check)

    result = await engine.run_harvest(
        ["https://www.tiktok.com/@acct"], engine.make_recent_n_selector(10), harvest_name="test",
    )

    # purge: old media files deleted, old sheet rows deleted, ledger record deleted
    assert set(patched_engine["deleted_files"]) == {"oldfile1", "oldfile2"}
    assert patched_engine["deleted_rows"] == [("acct-sheet", "1")]
    assert patched_engine["dedup_delete"] == [("tiktok", "1", "folder-acct")]
    # then re-harvested fresh
    assert result["summary"]["items_retried_failed"] == 1
    assert result["summary"]["items_collected"] == 1
    assert patched_engine["download"] == ["1"]


@pytest.mark.asyncio
async def test_single_item_failure_is_skipped_and_run_continues(patched_engine, monkeypatch):
    async def fake_list(profile_url, platform, max_items=30, stories_only=False):
        return {"profile": {"handle": "acct"}, "items": [_item("1"), _item("2")]}

    async def fake_download(content_id, source_url, is_video, content_type):
        if content_id == "1":
            raise RoachNotFoundError("content gone")
        return {"local_paths": ["/data/2.mp4"], "content_type": content_type}

    monkeypatch.setattr(engine, "social_profile_list", fake_list)
    monkeypatch.setattr(engine, "social_item_download", fake_download)

    result = await engine.run_harvest(
        ["https://www.tiktok.com/@acct"], engine.make_recent_n_selector(10), harvest_name="test",
    )

    assert result["error"] is None
    assert result["summary"]["items_collected"] == 1  # item 2 still delivered
    assert len(patched_engine["account_rows"]) == 1


@pytest.mark.asyncio
async def test_recent_n_selector_keeps_newest_n():
    items = [_item(str(i), published_at=f"2026-07-{i:02d}T00:00:00Z") for i in range(1, 6)]
    selected = engine.make_recent_n_selector(2)(items)
    assert [i["content_id"] for i in selected] == ["5", "4"]


@pytest.mark.asyncio
async def test_time_window_selector_excludes_old_items():
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    recent = _item("recent", published_at=(now - datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    old = _item("old", published_at=(now - datetime.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    selected = engine.make_time_window_selector(7)([recent, old])
    assert [i["content_id"] for i in selected] == ["recent"]


def test_cell_flattens_list_and_dict_values():
    # the model sometimes returns flow/subtitle as a JSON array — must not reach
    # the Sheets API as a list (HttpError 400)
    assert engine._cell(["1. hook", "2. point"]) == "1. hook\n2. point"
    assert engine._cell(None) == ""
    assert engine._cell("plain") == "plain"
    assert engine._cell(42) == 42


def test_row_core_stringifies_list_flow():
    item = {
        "content_id": "1", "content_type": "video", "source_url": "u", "published_at": "p",
        "caption": "c", "hashtags": ["a"], "public_counts": {"views": 5, "likes": 1, "comments": 0},
    }
    analysis = {"subtitle": "", "flow": ["step 1", "step 2"], "summary": "s"}
    core = engine._row_core("acct", "tiktok", item, analysis, ["f1"], "run", "2026-07-13")
    # every cell must be a scalar (no lists/dicts) or Sheets rejects the row
    assert all(not isinstance(v, (list, dict)) for v in core)
    assert core[13] == "step 1\nstep 2"  # content_flow flattened


def test_resolve_harvest_name_explicit_wins():
    assert engine._resolve_harvest_name("My Run") == "My Run"


def test_resolve_harvest_name_falls_back_outside_flow_run(monkeypatch):
    # Simulate being inside a flow run: runtime exposes the run name.
    import prefect.runtime.flow_run as fr
    monkeypatch.setattr(fr, "name", "graceful-rook", raising=False)
    assert engine._resolve_harvest_name(None) == "graceful-rook"


def test_date_range_selector_inclusive_bounds():
    items = [
        _item("before", published_at="2026-05-31T23:59:59Z"),
        _item("start_day", published_at="2026-06-01T00:00:00Z"),
        _item("mid", published_at="2026-06-15T12:00:00Z"),
        _item("end_day", published_at="2026-06-30T23:00:00Z"),
        _item("after", published_at="2026-07-01T00:00:01Z"),
    ]
    selected = engine.make_date_range_selector("2026-06-01", "2026-06-30")(items)
    # start and end days are inclusive; before/after excluded; newest-first order
    assert [i["content_id"] for i in selected] == ["end_day", "mid", "start_day"]


def test_date_range_selector_open_ended_start():
    items = [
        _item("old", published_at="2026-01-01T00:00:00Z"),
        _item("in", published_at="2026-06-15T00:00:00Z"),
        _item("after", published_at="2026-07-01T00:00:00Z"),
    ]
    # only an end bound: keep everything up to and including 2026-06-30
    selected = engine.make_date_range_selector(None, "2026-06-30")(items)
    assert sorted(i["content_id"] for i in selected) == ["in", "old"]
