"""Which file is a client's content plan (tasks/content_plan_files.py). Pure, no network.

Cases come from Drive as measured on 2026-09-24: a "Salinan" (copy) that the old
contains-match accepted, and two sheets with the identical Gudang Karung Jumbo name.
"""
import pytest

from tasks.content_plan_files import (
    AmbiguousPlanFileError,
    SPREADSHEET_MIME,
    missing_plan_columns,
    pick_plan_file,
    pick_plan_tab,
    plan_month_label,
)


def sheet(name, id_="x", **kw):
    return {"id": id_, "name": name, "mimeType": SPREADSHEET_MIME, **kw}


def test_exact_name_is_the_plan():
    files = [sheet("Content Plan - Klinik Utama Sumenep - September 2026", "real")]
    assert pick_plan_file(files, "Klinik Utama Sumenep", "September 2026")["id"] == "real"


def test_salinan_copy_is_never_the_plan_whatever_the_listing_order():
    copy = sheet("Salinan Content Plan - Klinik Utama Sumenep - September 2026", "copy")
    real = sheet("Content Plan - Klinik Utama Sumenep - September 2026", "real")
    assert pick_plan_file([copy, real], "Klinik Utama Sumenep", "September 2026")["id"] == "real"
    assert pick_plan_file([copy], "Klinik Utama Sumenep", "September 2026") is None


def test_whitespace_and_case_are_forgiven():
    files = [sheet("content plan -  Breko - OKTOBER 2026 ", "b")]
    assert pick_plan_file(files, "Breko", "Oktober 2026")["id"] == "b"


def test_two_sheets_with_the_same_name_stop_instead_of_guessing():
    files = [
        sheet("Content Plan - Gudang Karung Jumbo Sidoarjo - September 2026", "a", modifiedTime="2026-09-18T07:13:08Z"),
        sheet("Content Plan - Gudang Karung Jumbo Sidoarjo - September 2026", "b", modifiedTime="2026-08-19T00:00:00Z"),
    ]
    with pytest.raises(AmbiguousPlanFileError) as e:
        pick_plan_file(files, "Gudang Karung Jumbo Sidoarjo", "September 2026")
    assert "a (diubah 2026-09-18)" in str(e.value) and "b (diubah 2026-08-19)" in str(e.value)


def test_a_pdf_export_with_the_plan_name_is_not_the_plan():
    pdf = {"id": "pdf", "name": "Content Plan - MCafe - September 2026", "mimeType": "application/pdf"}
    real = sheet("Content Plan - MCafe - September 2026", "real")
    assert pick_plan_file([pdf, real], "MCafe", "September 2026")["id"] == "real"


def test_another_months_plan_is_not_this_months():
    files = [sheet("Content Plan - MCafe - Agustus 2026")]
    assert pick_plan_file(files, "MCafe", "September 2026") is None


def test_month_label_uses_the_indonesian_names_the_files_use():
    assert plan_month_label(2026, 10) == "Oktober 2026"
    assert plan_month_label(2026, 8) == "Agustus 2026"


def test_tab_rule_matches_the_jira_reader():
    assert pick_plan_tab(["Arsip", "Sheet1"]) == "Sheet1"
    assert pick_plan_tab(["sheet3"]) == "sheet3"
    assert pick_plan_tab([]) is None


def test_the_clients_roster_header_is_not_a_plan():
    assert missing_plan_columns(["Name", "Instagram", "Post", "Story"]) == ["Tanggal", "Bentuk", "Topik"]
    assert missing_plan_columns(["No.", "Tanggal", "Bentuk", "Topik"]) == []
