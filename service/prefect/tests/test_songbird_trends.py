"""
Tests for trend burst detection.

Pure and time-injected, so "recent vs baseline" is asserted deterministically.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tasks import songbird_trends as TR

NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)


def _row(text, days_ago, score=0.5):
    return {"caption": text, "hashtags": "", "published_at": NOW - timedelta(days=days_ago),
            "score": score}


def _tokens(trends):
    return [t["token"] for t in trends]


@pytest.mark.parametrize("noise", [
    "0811-6148-425",                 # phone number in caption boilerplate
    "klinikmatasampang.com",         # website
    "wa.me",
    "2026",
])
def test_contact_boilerplate_is_not_a_theme(noise):
    # "kami"/"sekarang" are stopwords, so only "hubungi" should survive.
    assert TR.tokenize(f"hubungi kami di {noise} sekarang") == ["hubungi"]


def test_tokenize_strips_trailing_punctuation():
    """'visus' and 'visus.' must not count as two different terms."""
    assert TR.tokenize("visus. visus") == ["visus", "visus"]


def test_tokenize_keeps_hashtags_and_drops_stopwords_and_shorts():
    tokens = TR.tokenize("Yuk periksa mata #lasik dengan dokter ahli 2026")
    assert "#lasik" in tokens
    assert "periksa" in tokens
    assert "yuk" not in tokens        # stopword
    assert "dengan" not in tokens     # stopword
    assert "2026" not in tokens       # bare number


def test_rising_token_is_detected():
    rows = [_row("promo katarak gratis", d) for d in (2, 5, 8, 11)]
    rows += [_row("layanan umum periksa", d) for d in range(25, 85, 5)]
    trends = TR.detect_trends(rows, now=NOW)
    assert "katarak" in _tokens(trends)


def test_steady_token_does_not_register_as_rising():
    """A term used at a constant rate is not a trend, however frequent."""
    rows = [_row("layanan katarak rutin", d) for d in range(2, 88, 4)]
    trends = TR.detect_trends(rows, now=NOW)
    assert "katarak" not in _tokens(trends)


def test_burst_requires_a_minimum_recent_count():
    """One viral post using a word is not a trend."""
    rows = [_row("istilah langka sekali", 3)]
    rows += [_row("konten biasa lainnya", d) for d in range(25, 85, 5)]
    trends = TR.detect_trends(rows, now=NOW)
    assert "langka" not in _tokens(trends)


def test_performance_weighting_ranks_landing_terms_higher():
    rows = [_row("topik bagus sekali", d, score=0.95) for d in (2, 4, 6)]
    rows += [_row("topik lemah sekali", d, score=0.05) for d in (3, 5, 7)]
    rows += [_row("konten lama netral", d) for d in range(25, 85, 5)]
    trends = TR.detect_trends(rows, now=NOW)
    tokens = _tokens(trends)
    assert tokens.index("bagus") < tokens.index("lemah")


def test_hashtag_and_bare_word_do_not_occupy_two_slots():
    """'#katarak' and 'katarak' are one term, not two trends."""
    rows = [{"caption": "promo katarak", "hashtags": "#katarak",
             "published_at": NOW - timedelta(days=d), "score": 0.8} for d in (2, 4, 6, 8)]
    rows += [_row("konten lama netral", d) for d in range(25, 85, 5)]
    tokens = _tokens(TR.detect_trends(rows, now=NOW))
    assert len([t for t in tokens if t.lstrip("#") == "katarak"]) == 1


def test_no_baseline_history_returns_empty():
    rows = [_row("konten baru saja", d) for d in (1, 2, 3)]
    assert TR.detect_trends(rows, now=NOW) == []


def test_rows_without_dates_are_skipped_safely():
    rows = [{"caption": "tanpa tanggal", "published_at": None}]
    assert TR.detect_trends(rows, now=NOW) == []


def test_format_trends_is_empty_when_nothing_rises():
    assert TR.format_trends([]) == ""


def test_format_trends_renders_counts():
    rendered = TR.format_trends([{"token": "#lasik", "recent_count": 5}])
    assert "#lasik" in rendered and "5x" in rendered
