"""
Tests for the songbird top-performer scoring/selection algorithm.

Pure unit tests — no DB, no network, no Prefect. Fixtures are shaped like the real
signal store (carousels/images carry likes only; video carries likes + comments +
views) so the regression tests reproduce the defects this algorithm replaces.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from tasks import songbird_ranking as R
from tasks.songbird_tasks import _is_same_client


# --------------------------------------------------------------------------
# Client identity — guards against grounding a plan in another client's facts
# --------------------------------------------------------------------------

@pytest.mark.parametrize("requested,candidate", [
    ("Klinik Mata Sampang", "Klinik Mata Sampang"),        # exact
    ("Klinik Utama Gasa", "Klinik Utama GASA"),            # casing
    ("LASIK Asyik by SMEC Tebet", "Lasik Asyik"),          # KB uses the short form
    ("Nirwana Coffee Space Pamekasan", "Nirwana Coffee Space"),
])
def test_same_client_is_recognised(requested, candidate):
    assert _is_same_client(requested, candidate)


@pytest.mark.parametrize("requested,candidate", [
    # The bug this guards: trigram similarity scored 0.467 for BOTH of these against
    # "klinik mata smec bitung" — a tie that grounded a Bitung plan in Bireuen's KB.
    ("Klinik Mata SMEC Bitung", "Klinik Mata Bireuen"),
    ("Klinik Mata SMEC Bitung", "Klinik Mata Sampang"),
    ("Klinik Mata Boyolali", "Klinik Mata Jogja"),
    ("Klinik Utama Gresik", "Klinik Utama Sumenep"),
    ("RS Mata SMEC Medan", "Klinik Mata Bireuen"),
])
def test_different_clients_are_rejected(requested, candidate):
    assert not _is_same_client(requested, candidate)


def test_blank_names_never_match():
    assert not _is_same_client("", "Klinik Mata Sampang")
    assert not _is_same_client("Klinik Mata Sampang", "")

NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)


def _post(profile, likes, *, days_ago=10, content_type="carousel", row_id=None):
    """A Post-bucket row: likes only, exactly as Instagram reports carousels/images."""
    return {
        "id": row_id or f"{profile}-p-{likes}-{days_ago}",
        "profile_key": profile, "content_type": content_type,
        "likes": likes, "comments": None, "views": None,
        "published_at": NOW - timedelta(days=days_ago),
    }


def _video(profile, likes, comments=0, views=10_000, *, days_ago=10, row_id=None):
    return {
        "id": row_id or f"{profile}-v-{likes}-{views}-{days_ago}",
        "profile_key": profile, "content_type": "video",
        "likes": likes, "comments": comments, "views": views,
        "published_at": NOW - timedelta(days=days_ago),
    }


# --------------------------------------------------------------------------
# Bucketing
# --------------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("carousel", "Post"), ("image", "Post"), ("video", "Short Video"),
    ("story", "Story"), ("Reels", "Short Video"), ("IG Story", "Story"),
])
def test_bucket_maps_harvested_content_types(raw, expected):
    assert R.bucket_of(raw) == expected


def test_unknown_content_type_is_kept_not_dropped():
    """An unexpected platform type must stay visible, not silently vanish."""
    assert R.bucket_of("livestream") == "livestream"


# --------------------------------------------------------------------------
# Percentiles
# --------------------------------------------------------------------------


def test_single_row_partition_scores_neutral_and_is_selectable():
    """percent_rank() would give 0 here, making the row permanently unselectable."""
    scored = R.score_rows([_post("solo", 10)], now=NOW)
    assert len(scored) == 1
    assert scored[0]["p_eng"] == pytest.approx(0.5)
    assert scored[0]["score"] > 0


def test_ties_use_midranks_and_are_deterministic():
    rows = [_post("a", 100, row_id=f"tie{i}") for i in range(4)]
    scored = R.score_rows(rows, now=NOW)
    percentiles = {round(r["p_eng"], 6) for r in scored}
    assert len(percentiles) == 1  # identical inputs -> identical scores

    again = R.score_rows([_post("a", 100, row_id=f"tie{i}") for i in range(4)], now=NOW)
    assert [r["score"] for r in scored] == [r["score"] for r in again]


def test_percentiles_are_shrunk_toward_neutral_for_small_partitions():
    small = R.score_rows([_post("a", i * 10) for i in range(1, 4)], now=NOW)
    large = R.score_rows([_post("b", i * 10) for i in range(1, 21)], now=NOW)
    assert max(r["p_eng"] for r in small) < max(r["p_eng"] for r in large)


def test_scores_are_mostly_relative_to_each_account_baseline():
    """
    A small account's breakout must score in the same league as a big account's —
    otherwise the largest competitor monopolizes every slot (the original defect).
    It is deliberately not *equal*: GLOBAL_BLEND anchors a minority of the score to
    absolute scale, because 12 interactions is thinner evidence than 800.
    """
    rows = [_post("big", v) for v in (200, 400, 800)] + [_post("small", v) for v in (3, 6, 12)]
    scored = R.score_rows(rows, now=NOW)
    best_big = max(r["score"] for r in scored if r["profile_key"] == "big")
    best_small = max(r["score"] for r in scored if r["profile_key"] == "small")
    assert best_small > 0.8 * best_big          # same league, not swamped
    assert best_small < best_big                # but absolute scale still counts


def test_absolute_noise_cannot_outrank_a_genuine_hit():
    """
    Pure within-account percentiles ranked a 5-interaction post above a 788-interaction
    one on live data. The global anchor is what prevents that.
    """
    rows = [_video("tiny", v, comments=0, views=900 + v) for v in (3, 4, 5)]
    rows += [_video("big", v, comments=10, views=30_000) for v in (200, 400, 788)]
    scored = R.score_rows(rows, now=NOW)
    best_tiny = max(r["score"] for r in scored if r["profile_key"] == "tiny")
    best_big = max(r["score"] for r in scored if r["profile_key"] == "big")
    assert best_big > best_tiny


def test_engagement_rate_never_boosts_a_low_reach_post_over_a_high_reach_one():
    """
    `rate = eng/views` is anti-correlated with views by construction (-0.60 measured),
    so as a positive term it cancelled the reach signal. It may only dampen.
    """
    rows = [
        _video("acct", 300, comments=10, views=500_000, row_id="reach"),   # low rate, huge reach
        _video("acct", 40, comments=2, views=800, row_id="tiny_high_rate"),  # great rate, no reach
        _video("acct", 30, comments=1, views=5_000),
    ]
    scored = {r["id"]: r for r in R.score_rows(rows, now=NOW)}
    assert scored["reach"]["score"] > scored["tiny_high_rate"]["score"]


# --------------------------------------------------------------------------
# Views / engagement rate
# --------------------------------------------------------------------------


def test_views_lift_high_reach_video():
    """The 296,975-view Reel case: reach must count, not just likes."""
    rows = [
        _video("acct", 171, comments=0, views=296_975, row_id="reach"),
        _video("acct", 400, comments=20, views=3_000, row_id="likes"),
        _video("acct", 60, comments=2, views=1_500),
        _video("acct", 30, comments=1, views=900),
    ]
    scored = {r["id"]: r for r in R.score_rows(rows, now=NOW)}
    assert scored["reach"]["p_views"] > scored["likes"]["p_views"]
    assert scored["reach"]["score"] > 0


def test_low_view_video_drops_rate_term_and_renormalizes():
    rows = [_video("acct", 10, views=40, row_id="tiny"), _video("acct", 50, views=9000)]
    scored = {r["id"]: r for r in R.score_rows(rows, now=NOW)}
    assert scored["tiny"]["engagement_rate"] is None   # below VIEW_FLOOR
    assert scored["tiny"]["p_rate"] is None
    assert 0 < scored["tiny"]["score"] <= 1            # renormalized, not zeroed


def test_posts_never_get_views_or_rate_terms():
    scored = R.score_rows([_post("a", 10), _post("a", 20)], now=NOW)
    assert all(r["p_views"] is None and r["p_rate"] is None for r in scored)


# --------------------------------------------------------------------------
# Recency / filtering
# --------------------------------------------------------------------------


def test_recency_breaks_ties_but_cannot_outweigh_performance():
    rows = [_post("a", 100, days_ago=200, row_id="old_star"),
            _post("a", 10, days_ago=1, row_id="fresh_dud"),
            _post("a", 50, days_ago=30)]
    scored = {r["id"]: r for r in R.score_rows(rows, now=NOW)}
    assert scored["old_star"]["score"] > scored["fresh_dud"]["score"]


def test_null_published_at_is_neutral_and_retained():
    rows = [dict(_post("a", 50), published_at=None, id="undated"), _post("a", 10)]
    scored = {r["id"]: r for r in R.score_rows(rows, now=NOW, window_days=180)}
    assert "undated" in scored
    assert scored["undated"]["age_days"] == pytest.approx(90.0)  # mid-window


def test_advertisement_rows_are_dropped_defensively():
    rows = [dict(_post("a", 500), advertisement=True, id="ad"), _post("a", 10)]
    assert {r["id"] for r in R.score_rows(rows, now=NOW)} == {"a-p-10-10"}


def test_min_engagement_floor_drops_noise_rows():
    rows = [_post("a", 1, row_id="noise"), _post("a", 50)]
    assert "noise" not in {r["id"] for r in R.score_rows(rows, now=NOW)}


# --------------------------------------------------------------------------
# Quota
# --------------------------------------------------------------------------


def test_quota_is_proportional_to_the_content_mix():
    assert R.exemplar_quota(12, {"Post": 4, "Story": 4, "Short Video": 4},
                            ["Post", "Story", "Short Video"]) == {"Post": 4, "Story": 4, "Short Video": 4}


def test_quota_follows_a_lopsided_mix():
    quota = R.exemplar_quota(12, {"Post": 8, "Short Video": 2}, ["Post", "Short Video"])
    assert sum(quota.values()) == 12
    assert quota["Post"] > quota["Short Video"]


def test_quota_without_a_mix_splits_evenly_across_present_buckets():
    quota = R.exemplar_quota(6, None, ["Post", "Short Video"])
    assert quota == {"Post": 3, "Short Video": 3}


def test_quota_gives_every_requested_type_at_least_one_slot():
    quota = R.exemplar_quota(10, {"Post": 20, "Story": 1}, ["Post", "Story"])
    assert quota["Story"] >= 1
    assert sum(quota.values()) == 10


# --------------------------------------------------------------------------
# Selection — the regression tests for the actual defects
# --------------------------------------------------------------------------


def test_video_no_longer_dominates_posts():
    """
    The core defect: a live query returned 8/8 video and zero Posts because
    Instagram reports comments+views only on video. Shaped like the real data.
    """
    rows = [_post("a", 5 + i) for i in range(10)] + [
        _video("a", 20 + i * 2, comments=3, views=5_000 + i * 100) for i in range(10)
    ]
    selected, stats = R.rank_performers(
        rows, total=8, content_mix={"Post": 4, "Short Video": 4}, now=NOW
    )
    assert stats["by_bucket"] == {"Post": 4, "Short Video": 4}
    assert len([r for r in selected if r["bucket"] == "Post"]) == 4


def test_per_account_cap_prevents_domination():
    """Previously 6 of 8 exemplars came from a single large account."""
    rows = [_post("whale", 100 + i, row_id=f"w{i}") for i in range(20)]
    rows += [_post("small_a", 5 + i, row_id=f"a{i}") for i in range(3)]
    rows += [_post("small_b", 6 + i, row_id=f"b{i}") for i in range(3)]
    selected, stats = R.rank_performers(rows, total=6, content_mix={"Post": 6}, now=NOW)
    assert stats["accounts"] == 3
    assert sum(1 for r in selected if r["profile_key"] == "whale") <= 3


def test_cap_relaxes_for_a_single_competitor_client():
    """One competitor + a fixed cap of 2 would return 2 of 6 exemplars."""
    rows = [_post("only", 10 + i, row_id=f"o{i}") for i in range(10)]
    selected, stats = R.rank_performers(rows, total=6, content_mix={"Post": 6}, now=NOW)
    assert len(selected) == 6
    assert stats["coverage"] == "full"


def test_shortfall_is_redistributed_and_reported_as_a_gap():
    """Story has ~2 rows in the entire store; the quota must not go unfilled."""
    rows = [_post("a", 10 + i, row_id=f"p{i}") for i in range(12)]
    selected, stats = R.rank_performers(
        rows, total=8, content_mix={"Post": 4, "Story": 4}, now=NOW
    )
    assert "Story" in stats["gaps"]
    assert stats["coverage"] == "partial"
    assert len(selected) == 8  # backfilled from Post rather than short-delivering


def test_empty_input_returns_empty_with_none_coverage():
    selected, stats = R.rank_performers([], total=8, content_mix={"Post": 8}, now=NOW)
    assert selected == []
    assert stats["coverage"] == "none"
    assert stats["candidates_considered"] == 0


def test_selection_is_ordered_and_ranked_within_bucket():
    rows = [_post("a", v, row_id=f"p{v}") for v in (10, 90, 50)]
    selected, _ = R.rank_performers(rows, total=3, content_mix={"Post": 3}, now=NOW)
    assert [r["id"] for r in selected] == ["p90", "p50", "p10"]
    assert [r["rank_within_bucket"] for r in selected] == [1, 2, 3]
