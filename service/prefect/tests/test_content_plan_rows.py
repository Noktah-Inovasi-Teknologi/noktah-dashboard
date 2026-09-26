"""
tasks/content_plan_rows.py: the Prefect copy of hub-api's plan fingerprint (spec 009 R4/R5).

hub-jira-create refuses a plan whose fingerprint differs from the one its Greenlight
recorded, and hub-api computed that one. If the two copies ever disagree, every plan is
refused (or a changed plan accepted), so parity is tested against hub-api's own module,
loaded by path (the services ship in different images and cannot import each other).
No network, no database.
"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tasks import content_plan_rows as rows_mod

API_PLANS = Path(__file__).resolve().parents[3] / "service" / "api" / "app" / "automation" / "plans.py"


@pytest.fixture(scope="module")
def api_plans():
    if not API_PLANS.exists():
        pytest.skip(f"hub-api source not found at {API_PLANS}")
    spec = importlib.util.spec_from_file_location("hub_api_automation_plans", API_PLANS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # plans.py is pure: stdlib imports only
    return module


SAMPLE_ROWS = [
    {"row_number": 2, "cells": {"No.": "1", "Tanggal": "04/10/2026", "Waktu": "10:00", "Bentuk": "Post",
                                "Topik": "Promo  LASIK\nOktober", "Caption": "Halo  semua ", "Approval": "OK",
                                "Key": "ESKL-12", "TicketID": "x"}},
    {"row_number": 3, "cells": {"Tanggal": "05/10/2026", "Bentuk": "Short Video", "Topik": "Tips mata sehat",
                                "Shoot Guide": "Scene 1: close up\n\nScene 2: wide", "Visualisasi Konten": "…",
                                "Link Referensi": "https://example.com/a"}},
    {"row_number": 4, "cells": {"Tanggal": "", "Topik": "", "Key": "ESKL-99"}},  # blank content, only a key
    {"row_number": 6, "cells": {"Tanggal": "07/10/2026", "Bentuk": "Story", "Topik": "Behind the scene 😊",
                                "Purpose/Theme": "Awareness", "Known Facts": None}},
    {"row_number": 7, "cells": {}},
]


def test_row_fingerprint_matches_hub_api(api_plans):
    for r in SAMPLE_ROWS:
        assert rows_mod.row_fingerprint(r["cells"]) == api_plans.row_fingerprint(r["cells"]), r["row_number"]


def test_plan_fingerprint_matches_hub_api(api_plans):
    assert rows_mod.plan_fingerprint(SAMPLE_ROWS) == api_plans.plan_fingerprint(SAMPLE_ROWS)
    assert rows_mod.plan_fingerprint([]) == api_plans.plan_fingerprint([])
    assert rows_mod.plan_fingerprint(SAMPLE_ROWS[:2]) == api_plans.plan_fingerprint(SAMPLE_ROWS[:2])


def test_content_rows_and_key_of_match_hub_api(api_plans):
    assert rows_mod.content_rows(SAMPLE_ROWS) == api_plans.content_rows(SAMPLE_ROWS)
    for r in SAMPLE_ROWS:
        assert rows_mod.key_of(r["cells"]) == api_plans.key_of(r["cells"])
    assert rows_mod.FINGERPRINT_COLUMNS == api_plans.FINGERPRINT_COLUMNS
    assert rows_mod.KEY_COLUMN == api_plans.KEY_COLUMN


def test_key_approval_and_ticket_changes_leave_fingerprint_alone():
    base = SAMPLE_ROWS[0]["cells"]
    changed = {**base, "Key": "ESKL-13", "Approval": "Revisi", "TicketID": "y", "No.": "9", "Keterangan": "z"}
    assert rows_mod.row_fingerprint(base) == rows_mod.row_fingerprint(changed)
    assert rows_mod.row_fingerprint(base) != rows_mod.row_fingerprint({**base, "Topik": "Lain"})


def test_rows_from_values_numbers_rows_from_two_and_drops_empty_rows():
    values = [
        ["No.", " Tanggal ", "Topik", "Bentuk", "", "Key", "Topik"],
        ["1", "04/10/2026", "A", "Post", "ignored", "", "duplicate header ignored"],
        [],
        ["", "", "", "", "", ""],
        ["3", "06/10/2026", "B"],
        ["", "", "", "", "", "ESKL-5"],
    ]
    header, rows = rows_mod.rows_from_values(values)
    assert header[1] == "Tanggal"
    assert [r["row_number"] for r in rows] == [2, 5, 6]
    assert rows[0]["cells"] == {"No.": "1", "Tanggal": "04/10/2026", "Topik": "A", "Bentuk": "Post", "Key": ""}
    assert rows[1]["cells"]["Bentuk"] == "" and rows[1]["cells"]["Key"] == ""
    assert rows_mod.key_of(rows[2]["cells"]) == "ESKL-5"


def test_key_cell_and_hyperlink():
    header = ["No.", "Tanggal"] + [f"c{i}" for i in range(25)] + ["Key"]
    assert rows_mod.key_cell_a1("Sheet1", header, 5) == "'Sheet1'!AB5"
    assert rows_mod.key_cell_a1("Rencana 'Okt'", ["Key"], 2) == "'Rencana ''Okt'''!A2"
    with pytest.raises(KeyError):
        rows_mod.key_cell_a1("Sheet1", ["Tanggal"], 2)
    assert rows_mod.key_hyperlink("https://x.atlassian.net/", "ESKL-7") == \
        '=HYPERLINK("https://x.atlassian.net/browse/ESKL-7","ESKL-7")'
