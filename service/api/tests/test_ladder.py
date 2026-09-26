"""Incentive Framework v2.1 §3.2 sanction ladder (spec 009 US9; ADR-0001)."""
from datetime import date, timedelta

import pytest

from app.incentive.ladder import Issued, active_sp, decide, direct

ON = date(2026, 11, 3)


def sp(level, days_ago, code=None):
    return Issued(level, ON - timedelta(days=days_ago), code)


def test_no_points_no_sanction():
    assert decide(0, 0, None, [], ON) is None


@pytest.mark.parametrize("points", [10, 20])
def test_10_to_20_is_a_verbal_warning(points):
    v = decide(points, 0, "C2", [], ON)
    assert (v.level, v.with_pip) == ("teguran_lisan", False)


def test_three_verbal_warnings_with_the_same_code_in_90_days_make_sp1():
    history = [sp("teguran_lisan", 70, "C2"), sp("teguran_lisan", 20, "C2")]
    assert decide(10, 0, "C2", history, ON).level == "sp1"
    assert decide(10, 0, "W7", history, ON).level == "teguran_lisan", "a different code doesn't add up"
    old = [sp("teguran_lisan", 120, "C2"), sp("teguran_lisan", 20, "C2")]
    assert decide(10, 0, "C2", old, ON).level == "teguran_lisan", "older than 90 days doesn't count"


@pytest.mark.parametrize("history, expected", [
    ([], "sp1"), ([sp("sp1", 30)], "sp2"), ([sp("sp2", 30)], "sp3"),
    ([sp("sp1", 100)], "sp1"),  # an SP older than 90 days has lapsed: the ladder starts again
])
def test_30_to_50_climbs_one_level_from_the_active_sp(history, expected):
    assert decide(30, 0, "C2", history, ON).level == expected


def test_60_or_more_is_one_level_up_with_a_pip():
    v = decide(60, 0, "C2", [], ON)
    assert (v.level, v.with_pip) == ("sp1", True)
    assert decide(60, 0, "C2", [sp("sp1", 10)], ON).level == "sp2"


def test_30_two_months_running_is_one_level_up_with_a_pip():
    v = decide(30, 40, "C2", [sp("sp1", 20)], ON)
    assert (v.level, v.with_pip) == ("sp2", True)


def test_violating_again_under_sp3_is_only_ever_flagged_for_termination():
    for history in ([sp("sp3", 10)], [sp("peringatan_terakhir", 10)]):
        v = decide(10, 0, "C2", history, ON)
        assert v.level == "phk_flag" and not v.with_pip


def test_the_heaviest_row_wins():
    history = [sp("teguran_lisan", 10, "C2"), sp("teguran_lisan", 5, "C2"), sp("sp2", 15)]
    assert decide(20, 0, "C2", history, ON).level == "sp1"  # 3 teguran with one code → SP1 …
    assert decide(40, 0, "C2", history, ON).level == "sp3"  # … but 30–50 with SP2 active is heavier


def test_active_sp_treats_the_final_warning_as_sp3():
    assert active_sp([sp("peringatan_terakhir", 5)], ON) == "sp3"
    assert active_sp([sp("teguran_lisan", 5)], ON) is None


@pytest.mark.parametrize("item, code, level", [
    ("4.3.1-2", "H2", "sp1"), ("4.3.2-1", "H2", "peringatan_terakhir"), ("4.3.3-4", "H2", "phk_flag"),
    (None, "H1", "peringatan_terakhir"),
])
def test_direct_sanctions_skip_the_ladder(item, code, level):
    assert direct(item, code).level == level


def test_an_h2_without_its_item_is_never_automatic():
    assert direct(None, "H2") is None
