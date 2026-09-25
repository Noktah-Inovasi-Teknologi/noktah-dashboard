"""
Tests for Jira ADF sanitisation (tasks/jira_adf.py).

These are the rules Jira enforces on a `description` document and that a
spreadsheet cell violates routinely -- a newline inside a text node, and an
empty text node from an unfilled column. Both are rejected as an opaque
INVALID_INPUT against the whole issue, which is why they cost content items
rather than showing up as a formatting warning.

Pure functions, no Prefect runtime and no network.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tasks import jira_adf as adf

LS = chr(0x2028)      # LINE SEPARATOR -- survives .strip(), still a newline to Jira
PS = chr(0x2029)      # PARAGRAPH SEPARATOR
NBSP = chr(0x00A0)    # arrives with every copy-paste from Docs
ZWSP = chr(0x200B)    # makes a "blank" cell non-empty


def texts(doc):
    """Every text node in a document, in order."""
    out = []

    def walk(node):
        if node.get("type") == "text":
            out.append(node["text"])
        for child in node.get("content") or []:
            walk(child)

    walk(doc)
    return out


def node_types(doc):
    out = []

    def walk(node):
        for child in node.get("content") or []:
            out.append(child.get("type"))
            walk(child)

    walk(doc)
    return out


# =============================================================================
# The two rules that break real content plans
# =============================================================================

@pytest.mark.parametrize("separator", ["\n", "\r\n", "\r", LS, PS, "\x0b", "\x0c", chr(0x85)])
def test_no_text_node_ever_contains_a_line_break(separator):
    doc = adf.document(adf.paragraphs_from_text(f"baris satu{separator}baris dua"))

    assert texts(doc) == ["baris satu", "baris dua"]
    assert "hardBreak" in node_types(doc)
    assert adf.find_adf_problems(doc) == []


@pytest.mark.parametrize("empty", ["", None, "   ", ZWSP, NBSP, float("nan")])
def test_an_unfilled_column_produces_no_text_node_at_all(empty):
    # `{"type": "text", "text": ""}` is what the old builder emitted here, and
    # Jira rejects it outright.
    doc = adf.document([adf.labelled_paragraph("PIC", empty)])

    assert texts(doc) == ["PIC: "]
    assert adf.find_adf_problems(doc) == []


def test_a_label_keeps_the_space_that_separates_it_from_its_value():
    # The bold run is "Approval: " with a trailing space; stripping it renders
    # as "Approval:Selesai".
    doc = adf.document([adf.labelled_paragraph("Approval", "Selesai")])

    assert texts(doc) == ["Approval: ", "Selesai"]


# =============================================================================
# Invisible characters
# =============================================================================

def test_invisible_characters_are_normalised_not_carried_through():
    value = f"Hook{NBSP}kuat{ZWSP} - 3 detik"

    assert adf.clean_text(value) == "Hook kuat - 3 detik"


def test_control_characters_and_lone_surrogates_are_removed():
    value = "caption\x00 dengan\x07 sampah" + chr(0xD800)

    cleaned = adf.clean_text(value)

    assert cleaned == "caption dengan sampah"
    assert adf.find_adf_problems(adf.document(adf.paragraphs_from_text(cleaned))) == []


def test_blank_line_runs_collapse_and_become_separate_paragraphs():
    doc = adf.document(adf.paragraphs_from_text("blok satu\n\n\n\nblok dua\nlanjutan"))

    assert [p["type"] for p in doc["content"]] == ["paragraph", "paragraph"]
    assert texts(doc) == ["blok satu", "blok dua", "lanjutan"]


# =============================================================================
# Summary
# =============================================================================

def test_summary_is_flattened_to_one_line():
    assert adf.clean_summary(f"Edukasi\ngigi{LS}berlubang") == "Edukasi gigi berlubang"


def test_summary_is_capped_at_jiras_limit():
    summary = adf.clean_summary("kata " * 200)

    assert len(summary) <= adf.MAX_SUMMARY_CHARS
    assert summary.endswith(adf.ELLIPSIS)


def test_summary_of_a_blank_cell_is_empty_so_the_caller_can_substitute():
    assert adf.clean_summary(f" {NBSP}{ZWSP} ") == ""


# =============================================================================
# Repair pass over documents this module did not build
# =============================================================================

def test_repairing_a_document_built_the_old_way_makes_it_valid():
    broken = {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [
                {"type": "text", "text": "Shoot Guide: ", "marks": [{"type": "strong"}]},
                {"type": "text", "text": "Scene 1\nScene 2"},
            ]},
            {"type": "paragraph", "content": [{"type": "text", "text": ""}]},
        ],
    }

    assert adf.find_adf_problems(broken)  # precondition: Jira would reject this

    repaired = adf.sanitize_document(broken)

    assert adf.find_adf_problems(repaired) == []
    assert texts(repaired) == ["Shoot Guide: ", "Scene 1", "Scene 2"]
    assert repaired["content"][0]["content"][0]["marks"] == [{"type": "strong"}]


def test_sanitising_is_idempotent():
    doc = adf.document(
        adf.labelled_block("Caption", "baris\nbaris")
        + [adf.labelled_paragraph("PIC", "")]
    )

    assert adf.sanitize_document(doc) == doc


def test_an_emptied_document_still_has_a_block_node():
    # An ADF doc with zero content is itself invalid.
    doc = adf.sanitize_document({"type": "doc", "version": 1, "content": []})

    assert doc["content"] == [{"type": "paragraph", "content": []}]
    assert adf.find_adf_problems(doc) == []


def test_a_node_that_requires_content_is_dropped_when_it_ends_up_empty():
    doc = adf.sanitize_document({
        "type": "doc", "version": 1,
        "content": [
            {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": ""}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "tetap ada"}]},
        ],
    })

    assert [b["type"] for b in doc["content"]] == ["paragraph"]


def test_an_oversized_document_is_truncated_rather_than_rejected():
    doc = adf.document(adf.paragraphs_from_text("x" * (adf.MAX_DOCUMENT_CHARS + 5000)))

    assert sum(len(t) for t in texts(doc)) <= adf.MAX_DOCUMENT_CHARS
    assert adf.find_adf_problems(doc) == []


# =============================================================================
# Issue-level cleaning
# =============================================================================

def test_sanitize_issue_cleans_summary_description_and_plain_fields():
    issue = {
        "fields": {
            "summary": f"Topik\nbaru{NBSP}",
            "description": {"type": "doc", "version": 1, "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "a\nb"}]},
            ]},
            "customfield_10039": "Short Video\n",
        },
        "metadata": {"original_row": {"Topik": "Topik\nbaru"}},
    }

    cleaned = adf.sanitize_issue(issue)

    assert cleaned["fields"]["summary"] == "Topik baru"
    assert texts(cleaned["fields"]["description"]) == ["a", "b"]
    assert cleaned["fields"]["customfield_10039"] == "Short Video"
    # The input is not mutated, and untouched keys survive.
    assert issue["fields"]["summary"] == f"Topik\nbaru{NBSP}"
    assert cleaned["metadata"]["original_row"]["Topik"] == "Topik\nbaru"


def test_a_plain_string_description_is_promoted_to_an_adf_document():
    cleaned = adf.sanitize_issue({"fields": {"description": "satu\ndua"}})

    assert cleaned["fields"]["description"]["type"] == "doc"
    assert texts(cleaned["fields"]["description"]) == ["satu", "dua"]


def test_only_the_summary_class_counts_as_fatal():
    """
    Measured against 21 production runs in service/prefect/data/: 10 of 675
    submitted issues were rejected and all 10 were summary newlines, while 1,461
    issues carrying a newline in a description text node were accepted. Treating
    the description findings as fatal would flag ~99% of rows and bury the class
    that actually costs content.
    """
    description_only = {
        "fields": {
            "summary": "Topik yang wajar",
            "description": {"type": "doc", "version": 1, "content": [
                {"type": "paragraph", "content": [
                    {"type": "text", "text": "baris\nbaris"},
                    {"type": "text", "text": ""},
                ]},
            ]},
        }
    }

    assert adf.find_fatal_problems(description_only) == []
    assert adf.find_issue_problems(description_only)  # still reported, just not fatal

    summary_newline = {"fields": {"summary": "Deteksi Retina\ndengan USG Mata"}}
    assert adf.find_fatal_problems(summary_newline) == ["summary contains a line break"]


@pytest.mark.parametrize("summary,expected", [
    ("   ", "summary is empty"),
    ("x" * 300, "summary is 300 characters (limit 255)"),
    ("topik\x07", "summary contains a control character"),
])
def test_the_other_fatal_summary_rules(summary, expected):
    assert expected in adf.find_fatal_problems({"fields": {"summary": summary}})


def test_find_issue_problems_names_what_jira_would_reject():
    problems = adf.find_issue_problems({
        "fields": {
            "summary": "a\nb",
            "description": {"type": "doc", "version": 1, "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": ""}]},
            ]},
        }
    })

    assert any("line break" in p for p in problems)
    assert any("empty text node" in p for p in problems)


# =============================================================================
# The converter itself
# =============================================================================

def test_converted_content_plan_row_is_accepted_shaped(monkeypatch):
    import logging

    from tasks import utility_tasks

    # Bypass get_run_logger, which needs a live Prefect task context.
    monkeypatch.setattr(utility_tasks, "get_run_logger", lambda: logging.getLogger("test"))

    row = {
        "Topik": f"Edukasi\ngigi berlubang{NBSP}",
        "Tanggal": "2026-09-10",
        "Waktu": "",
        "Bentuk": "Short Video\n",
        "Creator": "Brand",
        "Format": "Short Video",
        "Purpose/Theme": "Awareness",
        "Strategic Application": "",
        "Kebutuhan Personil": "",
        "Shoot Guide": f"Scene 1: hook 3 detik\r\nScene 2: penjelasan{LS}Scene 3: CTA",
        "Reference": "",
        "Asset": "",
        "Caption": f"Gigi ngilu?{ZWSP}\n\nYuk periksa.\n#dental",
        "Approval": "",
        "Link Referensi": "",
    }

    issue = utility_tasks.convert_content_plan_row_to_jira_issue.fn(
        row=row, client_name="Klinik Contoh", component_hashmap={"Klinik Contoh": "10100"}
    )

    assert adf.find_issue_problems(issue) == []
    assert issue["fields"]["summary"] == "Edukasi gigi berlubang"
    assert issue["fields"]["customfield_10039"] == "Short Video"
    # The multi-line Shoot Guide survives as separate lines, not as one blob.
    assert "Scene 3: CTA" in texts(issue["fields"]["description"])
    # Serialisable: no NaN, no lone surrogate.
    json.dumps(issue["fields"], ensure_ascii=False)


def test_converter_keeps_visualisasi_konten_when_shoot_guide_is_filled(monkeypatch):
    """
    "Shoot Guide" and "Visualisasi Konten" are two columns on the live sheet, side by
    side. The converter used to merge them with `or`, so a filled Shoot Guide silently
    dropped Visualisasi Konten from the ticket (60 rows in the saved runs), and an
    empty Shoot Guide put Visualisasi Konten under the wrong label. Each column must
    reach the description under its own label.
    """
    import logging

    from tasks import utility_tasks

    monkeypatch.setattr(utility_tasks, "get_run_logger", lambda: logging.getLogger("test"))

    base = {
        "Topik": "Operasi vitrectomy",
        "Tanggal": "2026-09-10",
        "Bentuk": "Short Video",
        "Shoot Guide": "Ambil di lobi, 3 angle",
        "Visualisasi Konten": "SCENE 1 - HOOK\nVISUAL:\nStaff menghadap kamera.\n\nSCENE 2\nVISUAL: CTA",
    }

    issue = utility_tasks.convert_content_plan_row_to_jira_issue.fn(
        row=base, client_name="Klinik Contoh", component_hashmap={"Klinik Contoh": "10100"}
    )
    desc = texts(issue["fields"]["description"])
    assert adf.find_issue_problems(issue) == []
    assert "Ambil di lobi, 3 angle" in desc
    assert "SCENE 1 - HOOK" in desc
    assert "VISUAL: CTA" in desc
    # Both labels present, in sheet order, each followed by its own value.
    assert desc.index("Shoot Guide: ") < desc.index("Ambil di lobi, 3 angle") \
        < desc.index("Visualisasi Konten: ") < desc.index("SCENE 1 - HOOK")

    # Empty Shoot Guide: Visualisasi Konten still lands under its own label,
    # not under "Shoot Guide".
    issue = utility_tasks.convert_content_plan_row_to_jira_issue.fn(
        row={**base, "Shoot Guide": ""}, client_name="Klinik Contoh",
        component_hashmap={"Klinik Contoh": "10100"},
    )
    desc = texts(issue["fields"]["description"])
    assert desc.index("Visualisasi Konten: ") < desc.index("SCENE 1 - HOOK")
    assert desc[desc.index("Shoot Guide: ") + 1] == "Visualisasi Konten: "

    # Pre-rename sheet with no Visualisasi Konten column at all still converts.
    row = {k: v for k, v in base.items() if k != "Visualisasi Konten"}
    issue = utility_tasks.convert_content_plan_row_to_jira_issue.fn(
        row=row, client_name="Klinik Contoh", component_hashmap={"Klinik Contoh": "10100"}
    )
    assert adf.find_issue_problems(issue) == []
