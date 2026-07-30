"""
Songbird top-performer scoring and exemplar selection (feature 003).

Pure functions — no I/O, no Prefect, no DB. `songbird_tasks.songbird_top_performers`
fetches candidate rows and delegates the ranking here, which keeps the algorithm
unit-testable (SQL is not, without a live database) and its constants tunable.

Why not a flat `ORDER BY (likes + comments) DESC`, which is what this replaces —
measured against the live signal store (526 rows, 12 Instagram accounts, ~90 days):

1. **Instagram only exposes `comments` and `views` on video.** Carousels and images
   report likes alone, and video already averages ~4x the engagement (30 vs 7), so a
   flat sum ranks by format, not merit. A live query for a 3-competitor set returned
   8/8 video and zero Posts — while the content plan asked for 4 Posts.
2. **Account baselines differ ~8x** (median 25 vs 3), so absolute engagement ranks
   accounts rather than content and the largest competitor takes every slot (6/8).
3. **`corr(views, likes) = 0.251`** — reach is nearly orthogonal to engagement, so a
   likes-only ranking is blind to it. A Reel with 296,975 views ranked 6th.

The scoring answers "did this overperform *for its account, in its format*" using
percentile ranks within `(profile_key, bucket)`: scale-free, robust to outliers, and
immune to the `"1.2K" -> 1200` quantization in `social_tasks._coerce_count`.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------
# Tunables
# --------------------------------------------------------------------------

# Weights for video buckets, where reach is observable. Non-video buckets score on
# engagement alone because `views`/`comments` are always NULL there.
WEIGHTS = {"eng": 0.50, "views": 0.50}

# Engagement rate (engagement / views) is Instagram's own "likes per reach" signal, but
# it CANNOT be a co-equal positive term: rate = eng/views is anti-correlated with views
# by construction (measured -0.60 on the live store), so adding it as a bonus cancels
# the reach signal and hands top rank to tiny-reach posts — a 972-view post with 5
# engagements outranked a 545,178-view Reel. It is therefore applied one-sidedly, as a
# dampener that only bites when the rate sits below the account's median: reach still
# counts (the hook travelled), but "travelled and nobody cared" is discounted.
RATE_NEUTRAL = 0.5   # at or above the account's median rate ⇒ no penalty
RATE_FLOOR = 0.75    # worst-case rate keeps 75% of the score

# Percentile-within-account makes a small account's best post tie a large account's
# best post. That is mostly the point — but taken alone it ranked a 5-engagement post
# above a 788-engagement one, and 5 interactions demonstrate no pattern worth adapting.
# A minority of the score is therefore anchored to the bucket's global distribution.
# This can afford to be substantial because *diversity is enforced by the per-account
# cap in `select_exemplars`, not by the score* — so anchoring cannot resurrect the
# one-account-takes-everything failure.
GLOBAL_BLEND = 0.30

# Percentiles from tiny partitions are mostly noise; shrink them toward neutral (0.5).
# n=1 -> no signal at all, n=20 -> ~0.87 of full spread.
SHRINK_K = 3

# Below this view count an engagement *rate* is not meaningful (3 likes on 40 views
# is not a 7.5% hit), so the rate term is dropped and its weight redistributed.
VIEW_FLOOR = 500

# Rows below this are indistinguishable from noise and carry no adaptable pattern.
MIN_ENGAGEMENT = 3

# Recency is a tilt, not a filter: a 90-day half-life decayed onto [RECENCY_FLOOR, 1]
# so an old outperformer still beats a fresh dud.
RECENCY_HALF_LIFE_DAYS = 90
RECENCY_FLOOR = 0.65

# Canonical content-plan vocabulary. Matches the Clients-worksheet column headers and
# the `Bentuk` values live content plans already use.
DEFAULT_VOCABULARY = ["Post", "Story", "Short Video"]

# Free-text `bentuk`/`content_type` mapped onto the canonical types. Used both for
# harvested `content_type` values (carousel/image/video/story) and for model-written
# `bentuk`. Only applied when the mapped target is actually allowed, so renaming a
# sheet column can never resurrect a type the client isn't paying for.
_BENTUK_SYNONYMS = {
    "post": "Post", "feed": "Post", "feed post": "Post", "carousel": "Post",
    "carousel post": "Post", "infographic": "Post", "infografis": "Post",
    "image": "Post", "foto": "Post", "single post": "Post", "static post": "Post",
    "story": "Story", "stories": "Story", "ig story": "Story",
    "instagram story": "Story", "insta story": "Story",
    "short video": "Short Video", "shortvideo": "Short Video", "reels": "Short Video",
    "reel": "Short Video", "video": "Short Video", "shorts": "Short Video",
    "tiktok": "Short Video", "short-form video": "Short Video",
}


def canonical_bentuk(value: str, allowed: Sequence[str]) -> Optional[str]:
    """Resolve a free-text content type to one of `allowed`, or None."""
    raw = (value or "").strip().lower()
    if not raw:
        return None
    by_lower = {a.strip().lower(): a for a in allowed}
    if raw in by_lower:
        return by_lower[raw]
    mapped = _BENTUK_SYNONYMS.get(raw)
    if mapped and mapped in allowed:
        return mapped
    # Last resort: a configured type named inside a longer phrase ("Reels Story").
    for lowered, original in by_lower.items():
        if lowered in raw:
            return original
    return None


def bucket_of(content_type: str, vocabulary: Sequence[str] = DEFAULT_VOCABULARY) -> str:
    """
    Map a harvested `content_type` onto the content-plan vocabulary.

    Unmapped types fall back to their own lowercased name rather than being dropped,
    so an unexpected platform type stays visible in the summary instead of vanishing.
    """
    return canonical_bentuk(content_type, vocabulary) or (content_type or "unknown").strip().lower()


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def _shrunk_percentiles(values: List[float]) -> List[float]:
    """
    Percentile position of each value within its partition, on (0, 1).

    Uses the Weibull position `rank / (n + 1)` with **midranks** for ties, then shrinks
    toward 0.5. Two deliberate choices:

    - Not `percent_rank()` (`(rank-1)/(n-1)`): it assigns 0 to the minimum and is
      undefined at n=1, so an account with a single post in the window would score 0
      and could never be selected. Here n=1 yields exactly 0.5 — neutral, selectable.
    - Midranks matter because `_coerce_count` quantizes "1.2K" to 1200, so ties cluster
      at the high end. Without them, selection among tied rows is arbitrary and unstable
      across runs.
    """
    n = len(values)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: values[i])
    positions = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        # Midrank of the tied block [i, j], 1-based.
        midrank = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            positions[order[k]] = midrank
        i = j + 1
    shrink = n / (n + SHRINK_K)
    return [0.5 + ((p / (n + 1)) - 0.5) * shrink for p in positions]


def _recency_multiplier(age_days: Optional[float], half_life_days: float) -> float:
    """Decay age onto [RECENCY_FLOOR, 1.0]; unknown age is treated as neutral."""
    if age_days is None or half_life_days <= 0:
        return 1.0
    decay = 0.5 ** (max(age_days, 0.0) / half_life_days)
    return RECENCY_FLOOR + (1.0 - RECENCY_FLOOR) * decay


def _age_days(published_at: Any, now: datetime, window_days: int) -> Optional[float]:
    """
    Age in days, or a neutral mid-window age when `published_at` is unknown.

    Undated rows are deliberately kept (the signal store never silently drops content),
    so they need an age that neither boosts nor buries them.
    """
    if not published_at:
        return window_days / 2.0
    if isinstance(published_at, str):
        try:
            published_at = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        except ValueError:
            return window_days / 2.0
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return max((now - published_at).total_seconds() / 86400.0, 0.0)


def score_rows(
    rows: List[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    window_days: int = 180,
    half_life_days: float = RECENCY_HALF_LIFE_DAYS,
    vocabulary: Sequence[str] = DEFAULT_VOCABULARY,
) -> List[Dict[str, Any]]:
    """
    Attach `bucket`, `engagement`, `engagement_rate`, `age_days` and `score` to each row.

    Rows are scored *within* their `(profile_key, bucket)` partition, so the output
    answers "how well did this do for this account, in this format" rather than
    "how big is this account".

    Advertisement rows and rows below MIN_ENGAGEMENT are dropped — the former because
    bought reach is not an organic pattern, the latter because they carry no signal.
    """
    now = now or datetime.now(timezone.utc)
    prepared: List[Dict[str, Any]] = []
    for row in rows:
        if row.get("advertisement") is True:
            continue
        likes = row.get("likes") or 0
        comments = row.get("comments") or 0
        views = row.get("views") or 0
        engagement = likes + comments
        if engagement < MIN_ENGAGEMENT:
            continue
        item = dict(row)
        item["bucket"] = bucket_of(str(row.get("content_type") or ""), vocabulary)
        item["engagement"] = engagement
        item["engagement_rate"] = (engagement / views) if views >= VIEW_FLOOR else None
        item["age_days"] = _age_days(row.get("published_at"), now, window_days)
        prepared.append(item)

    # Partition by (account, bucket) — the unit within which "overperformed" means something.
    partitions: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for item in prepared:
        partitions.setdefault((item.get("profile_key"), item["bucket"]), []).append(item)

    for (_, bucket), members in partitions.items():
        p_eng = _shrunk_percentiles([float(m["engagement"]) for m in members])
        video_like = bucket == "Short Video"
        p_views = (
            _shrunk_percentiles([float(m.get("views") or 0) for m in members]) if video_like else None
        )
        rated = [m for m in members if m["engagement_rate"] is not None]
        p_rate_by_id = {}
        if video_like and rated:
            for member, value in zip(rated, _shrunk_percentiles([m["engagement_rate"] for m in rated])):
                p_rate_by_id[id(member)] = value

        for index, member in enumerate(members):
            member["p_eng"] = p_eng[index]
            if not video_like:
                member["p_views"] = None
                member["p_rate"] = None
                member["base"] = p_eng[index]
            else:
                member["p_views"] = p_views[index]
                member["p_rate"] = p_rate_by_id.get(id(member))
                base = WEIGHTS["eng"] * p_eng[index] + WEIGHTS["views"] * p_views[index]
                if member["p_rate"] is not None:
                    base *= RATE_FLOOR + (1 - RATE_FLOOR) * min(
                        1.0, member["p_rate"] / RATE_NEUTRAL
                    )
                member["base"] = base

    # Global-within-bucket anchor, so "best of a tiny account" doesn't fully tie
    # "best of a large one" on evidence that is an order of magnitude thinner.
    for bucket in {m["bucket"] for m in prepared}:
        members = [m for m in prepared if m["bucket"] == bucket]
        p_global = _shrunk_percentiles([float(m["engagement"]) for m in members])
        for member, global_value in zip(members, p_global):
            member["p_global"] = global_value
            blended = (1 - GLOBAL_BLEND) * member["base"] + GLOBAL_BLEND * global_value
            member["score"] = blended * _recency_multiplier(member["age_days"], half_life_days)

    return prepared


# --------------------------------------------------------------------------
# Quota + selection
# --------------------------------------------------------------------------


def exemplar_quota(
    total: int, content_mix: Optional[Dict[str, int]], buckets_present: Sequence[str]
) -> Dict[str, int]:
    """
    Split `total` exemplar slots across buckets, proportional to the content mix.

    A plan asking for 4 Posts must see Post exemplars, so the exemplar budget mirrors
    the composition being generated. Largest-remainder apportionment, floor of 1 for
    every requested type. Without a mix, split evenly across the buckets that have rows.
    """
    if total <= 0:
        return {}
    if not content_mix:
        present = [b for b in buckets_present]
        if not present:
            return {}
        base, extra = divmod(total, len(present))
        return {b: base + (1 if i < extra else 0) for i, b in enumerate(present)}

    weights = {k: v for k, v in content_mix.items() if v > 0}
    if not weights:
        return {}
    denominator = sum(weights.values())
    exact = {k: total * v / denominator for k, v in weights.items()}
    quota = {k: max(1, int(math.floor(v))) for k, v in exact.items()}

    # Largest-remainder settle-up (the floors + the floor-of-1 can over- or undershoot).
    while sum(quota.values()) > total and len(quota) > 1:
        victim = min(quota, key=lambda k: (quota[k] <= 1, exact[k] - quota[k]))
        if quota[victim] <= 1 and sum(quota.values()) - 1 < len(quota):
            break
        quota[victim] -= 1
        if quota[victim] == 0:
            del quota[victim]
    while sum(quota.values()) < total:
        winner = max(quota, key=lambda k: exact[k] - quota[k])
        quota[winner] += 1
    return quota


def select_exemplars(
    scored: List[Dict[str, Any]], quota: Dict[str, int]
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Fill each bucket's quota, capping how much any one account can contribute.

    The cap is derived rather than fixed: `max(2, ceil(quota / accounts))`, relaxed if
    the bucket still underfills. A hard cap of 2 would starve single-competitor clients
    (most of them — the Hashmaps sheet configures competitors for only some clients),
    while no cap at all reproduces the 6-of-8-from-one-account failure.

    Returns:
        (selected rows ordered by bucket then score, stats for the run summary)
    """
    by_bucket: Dict[str, List[Dict[str, Any]]] = {}
    for row in scored:
        by_bucket.setdefault(row["bucket"], []).append(row)
    # Deterministic ordering: score, then recency, then id as a stable final tiebreak.
    for rows in by_bucket.values():
        rows.sort(key=lambda r: (-r["score"], r.get("age_days") or 0.0, str(r.get("id") or "")))

    selected: List[Dict[str, Any]] = []
    delivered: Dict[str, int] = {}
    gaps: List[str] = []

    for bucket, want in quota.items():
        candidates = by_bucket.get(bucket, [])
        accounts = len({c.get("profile_key") for c in candidates}) or 1
        cap = max(2, math.ceil(want / accounts))
        picked: List[Dict[str, Any]] = []
        # Relax the per-account cap only as far as needed to fill the quota.
        for effective_cap in (cap, cap + 1, want):
            per_account: Dict[str, int] = {}
            picked = []
            for candidate in candidates:
                key = candidate.get("profile_key")
                if per_account.get(key, 0) >= effective_cap:
                    continue
                picked.append(candidate)
                per_account[key] = per_account.get(key, 0) + 1
                if len(picked) >= want:
                    break
            if len(picked) >= want:
                break
        for rank, row in enumerate(picked, start=1):
            row["rank_within_bucket"] = rank
        selected.extend(picked)
        delivered[bucket] = len(picked)
        if len(picked) < want:
            gaps.append(bucket)

    # Redistribute any shortfall to buckets that still have unused candidates.
    shortfall = sum(quota.values()) - len(selected)
    if shortfall > 0:
        chosen = {id(r) for r in selected}
        spare = [r for r in scored if id(r) not in chosen]
        spare.sort(key=lambda r: (-r["score"], r.get("age_days") or 0.0, str(r.get("id") or "")))
        for row in spare[:shortfall]:
            selected.append(row)
            delivered[row["bucket"]] = delivered.get(row["bucket"], 0) + 1

    accounts_used = len({r.get("profile_key") for r in selected})
    if not selected:
        coverage = "none"
    elif gaps:
        coverage = "partial"
    else:
        coverage = "full"

    stats = {
        "quota": dict(quota),
        "by_bucket": delivered,
        "gaps": gaps,
        "coverage": coverage,
        "accounts": accounts_used,
    }
    return selected, stats


def rank_performers(
    rows: List[Dict[str, Any]],
    *,
    total: int,
    content_mix: Optional[Dict[str, int]] = None,
    window_days: int = 180,
    half_life_days: float = RECENCY_HALF_LIFE_DAYS,
    vocabulary: Sequence[str] = DEFAULT_VOCABULARY,
    now: Optional[datetime] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Score, stratify and select exemplars in one call.

    Returns:
        (selected rows, stats) where stats carries quota/by_bucket/gaps/coverage/
        accounts plus `candidates_considered` for the run summary (FR-011), and
        `scored` — the whole scored population, which trend detection needs to
        compare recent rates against a baseline rather than only the picks.
    """
    scored = score_rows(
        rows, now=now, window_days=window_days,
        half_life_days=half_life_days, vocabulary=vocabulary,
    )
    if not scored:
        return [], {
            "quota": {}, "by_bucket": {}, "gaps": [], "coverage": "none",
            "accounts": 0, "candidates_considered": len(rows), "scored": [],
        }
    buckets_present = sorted({r["bucket"] for r in scored})
    quota = exemplar_quota(total, content_mix, buckets_present)
    selected, stats = select_exemplars(scored, quota)
    stats["candidates_considered"] = len(rows)
    stats["scored"] = scored
    return selected, stats
