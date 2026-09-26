"""Incentive Framework v2.1 points from one judged Event (spec 009 US8; G-26, G-27, G-34, G-45, G-47)."""
from datetime import date, datetime, timezone

import pytest

from app.incentive import points

JUDGED = datetime(2026, 10, 5, 10, tzinfo=timezone.utc)
CREATED = datetime(2026, 10, 3, 9, tzinfo=timezone.utc)
CATEGORY = {"parent": "W - Process & Punctuality", "child": "W7 - Jadwal publish terlewat"}


def violation(**over):
    f = {"Event Type": "Violation", "Violation Judgment": "Kelalaian", "Event Observer": ["Internal (Staff)"],
         "Reporter's Own Mistake/Error": "No", "Problem/Error Solved": "Yes", "Known by the Person": "No",
         "Violation Category": CATEGORY, "Person": {"account_id": "acc-1", "name": "Nadya"}}
    f.update(over)
    return f


def run(fields, **kw):
    kw.setdefault("status", "Judged")
    kw.setdefault("judged_at", JUDGED)
    kw.setdefault("created_at", CREATED)
    return points.compute(fields, **kw)


@pytest.mark.parametrize("observer, own, solved, known, expected, formula", [
    ("Internal (Staff)", "Yes", "Yes", "No", 0, "Internal × self-report"),
    ("Internal (Staff)", "No", "Yes", "No", -10, "Internal × ditemukan orang lain"),
    ("Internal (Staff)", "No", "Yes", "Yes", -20, "Internal × ditemukan orang lain, sudah tahu"),
    ("External (Client)", "No", "No", "No", -30, "Klien × ditemukan orang lain"),
    ("External (Client)", "No", "Yes", "Yes", -60, "Klien × ditemukan orang lain, sudah tahu"),
])
def test_the_five_possible_violation_values(observer, own, solved, known, expected, formula):
    out = run(violation(**{"Event Observer": [observer], "Reporter's Own Mistake/Error": own,
                           "Problem/Error Solved": solved, "Known by the Person": known}))
    assert (out.state, out.points, out.formula) == ("counted", expected, formula)
    assert out.category == "W7" and out.letter == "W"


def test_a_self_report_whose_problem_isnt_solved_is_not_zero():
    out = run(violation(**{"Reporter's Own Mistake/Error": "Yes", "Problem/Error Solved": "No"}))
    assert out.points == -10


@pytest.mark.parametrize("judgment", ["Catatan sistem", "Skill gap dalam 30 hari", "Diajukan sebelum deadline"])
def test_anything_but_negligence_is_zero(judgment):
    out = run(violation(**{"Violation Judgment": judgment, "Event Observer": ["External (Client)"]}))
    assert (out.state, out.points) == ("zero", 0) and out.counts


@pytest.mark.parametrize("code, expected", [("X1", 1), ("X3", 1), ("X4", 3), ("X7", 3), ("X8", 1)])
def test_excellence_follows_its_category_and_is_positive(code, expected):
    out = run({"Event Type": "Excellence", "Excellence Category": [f"{code} - sesuatu"],
               "Person": {"account_id": "a"}})
    assert (out.state, out.points, out.kind) == ("counted", expected, "excellence")


@pytest.mark.parametrize("change, missing", [
    ({"Violation Judgment": None}, "Violation Judgment"),
    ({"Event Observer": []}, "Event Observer"),
    ({"Known by the Person": None}, "Known by the Person"),
    ({"Violation Category": None}, "Violation Category"),
])
def test_an_event_missing_what_it_needs_counts_nothing(change, missing):
    out = run(violation(**change))
    assert out.state == "belum_lengkap" and out.points == 0 and not out.counts
    assert missing in out.missing


def test_no_event_type_is_incomplete():
    assert run({"Person": {"account_id": "a"}}).state == "belum_lengkap"


def test_several_excellence_categories_add_up():
    out = run({"Event Type": "Excellence", "Excellence Category": ["X1 - a", "X4 - b"]})
    assert (out.state, out.points, out.formula) == ("counted", 4, "X1: +1 + X4: +3")


def test_both_observers_ticked_means_the_client_knew():
    out = run(violation(**{"Event Observer": ["Internal (Staff)", "External (Client)"]}))
    assert out.points == -30


def test_events_carry_no_defect_category():
    out = run(violation(**{"Violation Category": None,
                           "Defect Category": {"parent": "C - Design", "child": "C2 - Tidak sesuai"}}))
    assert out.state == "counted" and out.category == "C2", "a stray Defect Category is still read"


def test_an_event_without_a_matching_person_is_incomplete():
    out = run(violation(), person_known=False)
    assert out.state == "belum_lengkap" and "Person" in out.missing


def test_person_field_wins_over_assignee():
    assert points.person_account({"Person": {"account_id": "p"}, "Assignee": {"account_id": "a"}}) == "p"
    assert points.person_account({"Assignee": {"account_id": "a"}}) == "a"
    assert points.person_account({}) is None


def test_before_v21_counts_is_reference_only():
    out = run(violation(), judged_at=datetime(2026, 9, 26, 23, tzinfo=timezone.utc))
    assert out.state == "reference" and not out.counts and out.points == -10


def test_a_new_staff_members_first_30_days_carry_no_points():
    out = run(violation(), started_on=date(2026, 9, 20))
    assert (out.state, out.points) == ("adaptation", 0)
    assert run(violation(), started_on=date(2026, 9, 1)).state == "counted"


def test_direct_sanction_violations_apply_from_day_one():
    h2 = violation(**{"Defect Category": None,
                      "Violation Category": {"parent": "H - Attitude", "child": "H2 - Pelanggaran dengan sanksi langsung"},
                      "Direct Sanction Violation": "4.3.2-1 Posting ke akun Klien tanpa ACC Klien"})
    out = run(h2, started_on=date(2026, 9, 30))
    assert out.state == "counted" and out.category == "H2" and out.direct_item == "4.3.2-1"


def test_appealed_and_unjudged_events_wait():
    assert run(violation(), status="Appealed").state == "on_hold"
    assert run(violation(), status="Reported", judged_at=None).state == "not_judged"


def test_the_comment_carries_the_signed_points():
    assert points.comment_text(run(violation(**{"Event Observer": ["External (Client)"]}))).startswith(
        "Poin: -30 (Klien × ditemukan orang lain)")
    assert "masa adaptasi" in points.comment_text(run(violation(), started_on=date(2026, 9, 20)))
