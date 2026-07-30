"""
Tests for theme allocation — the variety-vs-doubling-down decision.

`allocate_slots` is pure and seeded, so the bandit behaviour is asserted directly:
thin evidence must spread slots, strong evidence must concentrate them, and neither
may breach the fatigue guard rails.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tasks import songbird_themes as T


def _theme(name, *scores):
    return {"theme": name, "scores": list(scores)}


def _counts(allocation):
    counts = {}
    for slot in allocation:
        counts[slot["theme"]] = counts.get(slot["theme"], 0) + 1
    return counts


def test_thin_evidence_spreads_slots_wide():
    """Nothing is proven yet, so the month should sample broadly, not commit."""
    themes = [_theme("A", 0.8), _theme("B", 0.5), _theme("C", 0.6)]
    allocation = T.allocate_slots(themes, 12, seed=1)
    assert len(allocation) == 12
    assert len(_counts(allocation)) == 3                      # every theme tried
    assert all(s["intent"] == "explore" for s in allocation)  # nothing is proven


def test_strong_evidence_concentrates_slots():
    """A theme with a real track record should get repeated — up to the cap."""
    themes = [_theme("winner", *[0.9] * 10)] + [
        _theme(name, *[0.2] * 10) for name in ("w1", "w2", "w3", "w4", "w5")
    ]
    allocation = T.allocate_slots(themes, 12, seed=7)
    counts = _counts(allocation)
    assert counts["winner"] == max(counts.values())
    assert counts["winner"] > min(counts.values())
    assert sum(1 for s in allocation if s["intent"] == "exploit") >= 6


def test_no_theme_exceeds_the_fatigue_cap():
    """Distinctiveness guard: one theme must never own the month."""
    themes = [_theme("dominant", *[0.99] * 30)] + [
        _theme(name, *[0.1] * 30) for name in ("a", "b", "c", "d")
    ]
    allocation = T.allocate_slots(themes, 10, seed=3)
    assert max(_counts(allocation).values()) <= max(1, int(10 * T.MAX_THEME_SHARE))


def test_cap_stays_feasible_when_there_are_too_few_themes():
    """
    With 2 themes and 10 slots a 40% cap leaves 2 slots unfillable; the cap must
    relax to an even split rather than overflow or under-deliver.
    """
    themes = [_theme("a", *[0.9] * 5), _theme("b", *[0.4] * 5)]
    allocation = T.allocate_slots(themes, 10, seed=11)
    assert len(allocation) == 10
    assert max(_counts(allocation).values()) <= 5


def test_untested_themes_still_get_slots_against_a_strong_incumbent():
    themes = [_theme("proven", *[0.95] * 20), _theme("fresh")]
    allocation = T.allocate_slots(themes, 10, seed=5)
    assert _counts(allocation).get("fresh", 0) >= 1
    assert any(s["intent"] == "explore" for s in allocation)


def test_allocation_is_reproducible_under_a_seed():
    themes = [_theme("A", *[0.8] * 5), _theme("B", *[0.3] * 5), _theme("C")]
    first = T.allocate_slots(themes, 8, seed=42)
    second = T.allocate_slots(themes, 8, seed=42)
    assert first == second


def test_no_themes_yields_free_slots_rather_than_failing():
    allocation = T.allocate_slots([], 4)
    assert len(allocation) == 4
    assert all(s["theme"] is None and s["intent"] == "explore" for s in allocation)


def test_zero_total_returns_nothing():
    assert T.allocate_slots([_theme("A", 0.5)], 0) == []


def test_posterior_narrows_as_evidence_accumulates():
    _, thin = T._posterior([0.6])
    _, thick = T._posterior([0.6] * 30)
    assert thick < thin


def test_unobserved_theme_gets_a_wide_neutral_prior():
    mean, spread = T._posterior([])
    assert mean == T.PRIOR_MEAN
    assert spread == T.PRIOR_SPREAD


@pytest.mark.parametrize("raw,expected", [
    ("0,3,7", [0, 3, 7]),
    ("0, 3 , 7", [0, 3, 7]),
    ("0;3", [0, 3]),
    ("0,99,3", [0, 3]),      # out-of-range dropped
    ("abc", []),
    ("", []),
    ("2,2,2", [2]),          # deduped
])
def test_index_parsing_is_defensive(raw, expected):
    assert T._parse_indexes(raw, upper=10) == expected


def test_pillars_become_untested_arms():
    themes = T.themes_from_pillars(["Edukasi", "Testimoni", "  "])
    assert [t["theme"] for t in themes] == ["Edukasi", "Testimoni"]
    assert all(t["scores"] == [] for t in themes)
