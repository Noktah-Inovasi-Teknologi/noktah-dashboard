"""
Tests for the sheet-backed hashmaps (hashmap.py).

The Sheets API is mocked; what's under test is the parsing of the "Hashmaps"
worksheet layout, the caching/snapshot fallback, and the lazy dict-compatible
mappings the rest of the service imports.
"""
import json

import pytest

import hashmap


# Mirrors the real sheet's shape: fixed columns, ragged/variable-length rows,
# blocks of different lengths, blank filler columns (D/H/L/P).
SAMPLE_ROWS = [
    # A     B                C            D   E    F              G        H   I    J              K            L   M    N             O           P   Q    R              S              T
    ["1", "Nadya Safira", "712020:nad", "", "1", "Balakosa", "10034", "", "1", "Klinik Sampang", "Putri Indah", "", "1", "Klinik Boyolali", "Halimatudz", "", "1", "LASIK Asyik", "Ciputra SMG", "https://www.instagram.com/lasikindonesia/"],
    ["2", "Putri Indah", "712020:put", "", "2", "Ecky Dental", "10000", "", "2", "Ecky Dental", "Putri Indah", "", "2", "Ecky Dental", "Siti Nurhayati", "", "2", "LASIK Asyik", "KMN EyeCare", "https://www.instagram.com/kmneyecare/"],
    ["3", "Halimatudz", "712020:hal", "", "3", "Klinik Sampang", "10006"],
    ["", "", "", "", "4", "Klinik Boyolali", "10008", "", "", "", "", "", "", "", "", "", "4", "Ecky Dental", "Sebaya Dental", "https://www.instagram.com/sebayadental/"],
]


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Give every test a clean in-memory cache and its own snapshot file."""
    monkeypatch.setattr(hashmap, "_cache", {"blocks": None, "fetched_at": 0.0})
    monkeypatch.setattr(hashmap, "HASHMAP_CACHE_PATH", tmp_path / "hashmap_cache.json")
    return tmp_path


@pytest.fixture
def sheet(monkeypatch):
    """Stub the Sheets read; `calls` counts fetches, `rows`/`error` drive the result."""
    state = {"calls": 0, "rows": SAMPLE_ROWS, "error": None}

    def fake_read_rows():
        state["calls"] += 1
        if state["error"]:
            raise state["error"]
        return state["rows"]

    monkeypatch.setattr(hashmap, "_read_rows", fake_read_rows)
    return state


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def test_parses_every_block_from_the_fixed_columns():
    blocks = hashmap.parse_hashmap_rows(SAMPLE_ROWS)

    assert blocks["WORKERS"] == {
        "Nadya Safira": "712020:nad",
        "Putri Indah": "712020:put",
        "Halimatudz": "712020:hal",
    }
    assert blocks["COMPONENTS"] == {
        "Balakosa": "10034",
        "Ecky Dental": "10000",
        "Klinik Sampang": "10006",
        "Klinik Boyolali": "10008",
    }
    assert blocks["CONTENT_EDITOR"] == {
        "Klinik Sampang": "Putri Indah",
        "Ecky Dental": "Putri Indah",
    }
    assert blocks["FIELD_ASSOCIATE"] == {
        "Klinik Boyolali": "Halimatudz",
        "Ecky Dental": "Siti Nurhayati",
    }


def test_ragged_rows_do_not_break_wider_blocks():
    """Row 3 is short (7 cells) — the CLIENT_SOCIAL columns simply read as blank."""
    blocks = hashmap.parse_hashmap_rows(SAMPLE_ROWS)
    assert "Klinik Sampang" in blocks["COMPONENTS"]
    assert set(blocks["CLIENT_SOCIAL"]) == {"LASIK Asyik", "Ecky Dental"}


def test_client_social_groups_competitor_rows_per_client():
    social = hashmap.parse_hashmap_rows(SAMPLE_ROWS)["CLIENT_SOCIAL"]

    assert social["LASIK Asyik"]["competitors"] == ["lasikindonesia", "kmneyecare"]
    assert social["LASIK Asyik"]["own"] == []
    assert social["Ecky Dental"]["competitors"] == ["sebayadental"]
    assert social["LASIK Asyik"]["competitor_profiles"][0] == {
        "name": "Ciputra SMG",
        "url": "https://www.instagram.com/lasikindonesia/",
        "handle": "lasikindonesia",
    }


def test_client_social_deduplicates_repeated_competitors():
    rows = SAMPLE_ROWS + [["", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "",
                          "5", "Ecky Dental", "Sebaya Dental", "https://www.instagram.com/sebayadental/"]]
    social = hashmap.parse_hashmap_rows(rows)["CLIENT_SOCIAL"]
    assert social["Ecky Dental"]["competitors"] == ["sebayadental"]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("https://www.instagram.com/lasikindonesia/", "lasikindonesia"),
        ("https://www.instagram.com/mcafespace/?hl=en", "mcafespace"),
        ("https://www.tiktok.com/@karungjumbosidoarjo", "karungjumbosidoarjo"),
        ("www.instagram.com/klinikutama.sumenep/", "klinikutama.sumenep"),
        ("@Handle", "handle"),
        ("BareHandle", "barehandle"),
        ("-", ""),
        ("", ""),
    ],
)
def test_profile_handle_matches_harvested_signals_profile_key(value, expected):
    assert hashmap.profile_handle(value) == expected


def test_rows_missing_a_value_are_skipped_not_stored_blank():
    rows = [["1", "Someone", ""], ["2", "", "712020:x"], ["3", "Valid", "712020:v"]]
    assert hashmap.parse_hashmap_rows(rows)["WORKERS"] == {"Valid": "712020:v"}


def test_column_letters_map_to_zero_based_indexes():
    assert [hashmap._col_index(c) for c in ("A", "B", "T", "Z", "AA")] == [0, 1, 19, 25, 26]


# --------------------------------------------------------------------------
# Caching / fallback
# --------------------------------------------------------------------------


def test_sheet_is_read_once_within_the_ttl(sheet, monkeypatch):
    monkeypatch.setattr(hashmap, "HASHMAP_TTL_SECONDS", 300)
    hashmap.load_hashmaps()
    hashmap.load_hashmaps()
    assert sheet["calls"] == 1


def test_expired_ttl_and_force_both_re_read(sheet, monkeypatch):
    monkeypatch.setattr(hashmap, "HASHMAP_TTL_SECONDS", 0)
    hashmap.load_hashmaps()
    hashmap.load_hashmaps()
    assert sheet["calls"] == 2

    monkeypatch.setattr(hashmap, "HASHMAP_TTL_SECONDS", 300)
    hashmap.refresh_hashmaps()
    assert sheet["calls"] == 3


def test_successful_fetch_writes_a_snapshot(sheet):
    hashmap.load_hashmaps()
    payload = json.loads(hashmap.HASHMAP_CACHE_PATH.read_text(encoding="utf-8"))
    assert payload["blocks"]["COMPONENTS"]["Ecky Dental"] == "10000"


def test_snapshot_is_used_when_the_sheet_is_unreachable(sheet, monkeypatch):
    hashmap.load_hashmaps()  # seeds the snapshot
    monkeypatch.setattr(hashmap, "_cache", {"blocks": None, "fetched_at": 0.0})
    sheet["error"] = RuntimeError("Sheets API down")

    blocks = hashmap.load_hashmaps()
    assert blocks["COMPONENTS"]["Ecky Dental"] == "10000"


def test_missing_sheet_and_missing_snapshot_raises(sheet):
    sheet["error"] = RuntimeError("Sheets API down")
    with pytest.raises(RuntimeError, match="no local snapshot"):
        hashmap.load_hashmaps()


def test_refresh_reports_entry_counts(sheet):
    assert hashmap.refresh_hashmaps() == {
        "WORKERS": 3, "COMPONENTS": 4, "CONTENT_EDITOR": 2,
        "FIELD_ASSOCIATE": 2, "CLIENT_SOCIAL": 2,
    }


# --------------------------------------------------------------------------
# Lazy mappings (the import-compatible surface)
# --------------------------------------------------------------------------


def test_importing_the_module_does_not_touch_the_sheet(sheet):
    """The proxies must stay inert until something actually reads them."""
    assert sheet["calls"] == 0
    repr(hashmap.WORKERS)
    assert sheet["calls"] == 0

    hashmap.WORKERS.get("Nadya Safira")
    assert sheet["calls"] == 1


def test_mappings_behave_like_the_dicts_they_replaced(sheet):
    assert hashmap.WORKERS.get("Nadya Safira") == "712020:nad"
    assert hashmap.WORKERS["Putri Indah"] == "712020:put"
    assert hashmap.WORKERS.get("Nobody", "") == ""
    assert "Ecky Dental" in hashmap.COMPONENTS
    assert len(hashmap.COMPONENTS) == 4
    assert sorted(hashmap.CONTENT_EDITOR) == ["Ecky Dental", "Klinik Sampang"]
    assert dict(hashmap.FIELD_ASSOCIATE)["Ecky Dental"] == "Siti Nurhayati"
    assert hashmap.CLIENT_SOCIAL.get("Nobody", {}).get("competitors", []) == []
