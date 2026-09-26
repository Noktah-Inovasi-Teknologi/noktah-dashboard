"""Content Plan rules and the watcher's findings (spec 009 US1/US2; research R4/R5). No database."""
from datetime import date

from app.automation import plans, watcher

MONTH = date(2026, 10, 1)


def row(n, **cells):
    base = {"Tanggal": f"{n:02d}/10/2026", "Bentuk": "Post", "Topik": f"Topik {n}", "Caption": "c"}
    base.update(cells)
    return {"row_number": n, "cells": base}


def test_writing_the_key_or_ticking_approval_is_not_a_change():
    a = [row(2), row(3)]
    b = [row(2, Key="ESKL-1", Approval="OK", TicketID="x", Keterangan="catatan"), row(3)]
    assert plans.plan_fingerprint(a) == plans.plan_fingerprint(b)
    assert plans.plan_fingerprint(a) != plans.plan_fingerprint([row(2, Topik="Lain"), row(3)])


def test_blank_rows_are_ignored_and_whitespace_doesnt_count():
    assert plans.plan_fingerprint([row(2)]) == plans.plan_fingerprint([row(2), {"row_number": 9, "cells": {}}])
    assert plans.row_fingerprint({"Topik": "a  b "}) == plans.row_fingerprint({"Topik": "a b"})


def test_key_is_read_from_the_cell_text():
    assert plans.key_of({"Key": "ESKL-11179"}) == "ESKL-11179"
    assert plans.key_of({"Key": " "}) is None


def test_assess_blocks_on_missing_team_component_and_fields():
    rows = [row(2), row(3, Topik="")]
    blocking, warnings, quota = plans.assess(rows, MONTH, {"Post": 2, "Story": 0, "Short Video": None},
                                             has_field_associate=False, has_content_editor=True, fa_has_jira=False,
                                             ce_has_jira=False, has_component=False)
    codes = [b["code"] for b in blocking]
    assert codes == ["no_field_associate", "no_jira_account", "no_component", "row_missing_fields"]
    assert quota["Post"] == {"planned": 2, "quota": 2} and not [w for w in warnings if "Post" in w["message"]]


def test_assess_warns_on_quota_and_dates_outside_the_month():
    rows = [row(2), row(3, Tanggal="01/11/2026", Bentuk="Reels")]
    blocking, warnings, quota = plans.assess(rows, MONTH, {"Post": 4, "Story": None, "Short Video": 1},
                                             has_field_associate=True, has_content_editor=True, fa_has_jira=True,
                                             ce_has_jira=True, has_component=True)
    assert blocking == []
    assert {w["code"] for w in warnings} == {"quota_mismatch", "date_outside_month"}
    assert quota["Short Video"]["planned"] == 1


def _issue(r, key):
    return {"issue_key": key, "fingerprint": plans.row_fingerprint(r["cells"]), "last_fingerprint": None,
            "cells": r["cells"]}


def test_a_changed_keyed_row_is_found_with_old_and_new():
    before = [row(2, Key="ESKL-1")]
    after = [row(2, Key="ESKL-1", Tanggal="22/10/2026")]
    [f] = watcher.inspect(after, before, [_issue(before[0], "ESKL-1")])
    assert f.kind == "changed_after_issue" and f.issue_key == "ESKL-1"
    assert f.changes == [{"column": "Tanggal", "old": "02/10/2026", "new": "22/10/2026"}]


def test_an_unchanged_plan_finds_nothing_and_new_rows_just_wait():
    r = row(2, Key="ESKL-1")
    assert watcher.inspect([r, row(3)], [r], [_issue(r, "ESKL-1")]) == []


def test_deleted_erased_duplicated_and_unknown_keys():
    r2, r3 = row(2, Key="ESKL-1"), row(3, Key="ESKL-2")
    issues = [_issue(r2, "ESKL-1"), _issue(r3, "ESKL-2")]
    erased = dict(r3, cells={k: v for k, v in r3["cells"].items() if k != "Key"})
    kinds = {(f.kind, f.issue_key) for f in watcher.inspect([erased], [r2, r3], issues)}
    assert kinds == {("deleted_from_plan", "ESKL-1"), ("key_erased", "ESKL-2")}
    dup = row(4, Key="ESKL-1", Topik="Salinan")
    kinds = {(f.kind, f.issue_key) for f in watcher.inspect([r2, r3, dup, row(5, Key="ESKL-99")], [r2, r3], issues)}
    assert ("key_duplicated", "ESKL-1") in kinds and ("key_unknown", "ESKL-99") in kinds


def test_the_comment_lists_each_change():
    text = watcher.comment_text("Content Plan - X - Oktober 2026", 5,
                                [{"column": "Tanggal", "old": "18/10/2026", "new": "22/10/2026"}])
    assert "baris 5" in text and "Tanggal: 18/10/2026 → 22/10/2026" in text and "tidak diubah otomatis" in text
