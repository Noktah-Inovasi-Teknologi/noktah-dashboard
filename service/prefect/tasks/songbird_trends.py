"""
Songbird trend detection — what is rising right now.

The ranking module answers "what performed well over the window"; this answers the
different question of "what is *accelerating*", so a monthly plan can ride a topic
while it is still climbing instead of only mirroring the last six months.

Kleinberg-style burst detection, simplified: model each token's occurrences as a
Poisson process, compare its rate in a recent window against the preceding baseline,
and score the surprise. Weighted by how well the posts carrying the token actually
performed, so a term that is merely frequent doesn't outrank one that is frequent
*and* landing.

Pure functions — the caller supplies rows. Best-effort by contract: a caller that
gets [] simply generates without a trend block (FR-011).
"""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Recent vs baseline split of the analysis window.
RECENT_DAYS = 21
BASELINE_DAYS = 69

# A token must appear at least this often in the recent window to be considered — one
# viral post using a word is not a trend.
MIN_RECENT_COUNT = 3

# Tokens shorter than this are noise once stopwords are removed.
MIN_TOKEN_LENGTH = 4

# Indonesian + English function words, plus social-media filler that is frequent in
# every caption and therefore never informative about a trend.
STOPWORDS = {
    "yang", "untuk", "dengan", "dari", "pada", "adalah", "akan", "atau", "juga", "dalam",
    "tidak", "bisa", "sudah", "lebih", "agar", "karena", "saja", "oleh", "kami", "kita",
    "anda", "kamu", "mereka", "ini", "itu", "ada", "jadi", "dan", "yuk", "nih", "aja",
    "biar", "kalau", "kalo", "banget", "sangat", "punya", "buat", "kena", "supaya",
    "harus", "masih", "bagi", "para", "setiap", "hingga", "sampai", "sehingga", "namun",
    "tetapi", "tapi", "kalian", "hanya", "dapat", "telah", "melalui", "tanpa", "serta",
    "the", "and", "for", "with", "you", "your", "from", "that", "this", "are", "our",
    "have", "has", "can", "will", "not", "but", "all", "more", "about", "into", "out",
    "info", "yaa", "yang", "hari", "kali", "orang", "banyak", "sekarang", "gratis",
    "jangan", "mari", "lewat", "yaitu", "seperti", "bila", "saat", "ketika", "utama",
    "sebelum", "setelah", "sebagai", "salah", "satu", "lain", "lainnya", "segera",
}

_TOKEN_RE = re.compile(r"[#\w][\w\.\-']*", re.UNICODE)

# Contact boilerplate repeated in captions ("0811-6148-425", "klinikmatasampang.com")
# bursts like a real term but is never a content theme.
_DIGIT_RUN_RE = re.compile(r"\d{3,}")
_DOMAIN_RE = re.compile(r"\.(com|net|org|id|co|co\.id|my\.id|info|biz|me|link|site)$")


def _is_noise_token(token: str) -> bool:
    """Phone numbers, domains and anything mostly numeric are not themes."""
    bare = token.lstrip("#")
    if _DIGIT_RUN_RE.search(bare) or _DOMAIN_RE.search(bare):
        return True
    digits = sum(c.isdigit() for c in bare)
    return digits * 2 >= len(bare)


def tokenize(text: str) -> List[str]:
    """Lowercased content tokens; hashtags keep their '#' so they stay distinguishable."""
    tokens = []
    for raw in _TOKEN_RE.findall(str(text or "").lower()):
        # Trailing/leading punctuation would otherwise split one term into several
        # ("visus" vs "visus."), diluting its rate and hiding a real burst.
        raw = raw.strip(".-'")
        if _is_noise_token(raw):
            continue
        if raw.startswith("#"):
            if len(raw) > 2:
                tokens.append(raw)
            continue
        if len(raw) < MIN_TOKEN_LENGTH or raw in STOPWORDS:
            continue
        tokens.append(raw)
    return tokens


def _age_days(published_at: Any, now: datetime) -> Optional[float]:
    if not published_at:
        return None
    if isinstance(published_at, str):
        try:
            published_at = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        except ValueError:
            return None
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return (now - published_at).total_seconds() / 86400.0


def detect_trends(
    rows: List[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    recent_days: int = RECENT_DAYS,
    baseline_days: int = BASELINE_DAYS,
    limit: int = 8,
    min_recent_count: int = MIN_RECENT_COUNT,
) -> List[Dict[str, Any]]:
    """
    Score tokens by how far their recent rate exceeds their baseline rate.

    Args:
        rows: harvested rows with `caption`, `hashtags`, `published_at`, and
            optionally `score` (the ranking percentile) to weight by performance.

    Returns:
        [{"token", "burst", "recent_count", "baseline_count", "lift"}, ...],
        strongest first. Empty when there is not enough history to compare.
    """
    now = now or datetime.now(timezone.utc)
    recent_docs: List[List[str]] = []
    recent_scores: List[float] = []
    baseline_docs: List[List[str]] = []

    for row in rows:
        age = _age_days(row.get("published_at"), now)
        if age is None or age < 0:
            continue
        text = f"{row.get('caption') or ''} {row.get('hashtags') or ''}"
        tokens = set(tokenize(text))
        if not tokens:
            continue
        if age <= recent_days:
            recent_docs.append(list(tokens))
            recent_scores.append(float(row.get("score") or 0.5))
        elif age <= recent_days + baseline_days:
            baseline_docs.append(list(tokens))

    if not recent_docs or not baseline_docs:
        logger.info(
            f"Trend detection skipped: {len(recent_docs)} recent / {len(baseline_docs)} "
            f"baseline documents is not enough to compare"
        )
        return []

    recent_weeks = max(recent_days / 7.0, 1e-6)
    baseline_weeks = max(baseline_days / 7.0, 1e-6)

    recent_counts: Dict[str, int] = {}
    recent_score_sum: Dict[str, float] = {}
    for tokens, score in zip(recent_docs, recent_scores):
        for token in tokens:
            recent_counts[token] = recent_counts.get(token, 0) + 1
            recent_score_sum[token] = recent_score_sum.get(token, 0.0) + score

    baseline_counts: Dict[str, int] = {}
    for tokens in baseline_docs:
        for token in tokens:
            baseline_counts[token] = baseline_counts.get(token, 0) + 1

    trends = []
    for token, count in recent_counts.items():
        if count < min_recent_count:
            continue
        base_count = baseline_counts.get(token, 0)
        recent_rate = count / recent_weeks
        baseline_rate = base_count / baseline_weeks
        # Poisson-ish surprise: how many standard deviations above the baseline rate,
        # with a +1 prior so a brand-new token doesn't divide by zero.
        burst = (recent_rate - baseline_rate) / math.sqrt((baseline_rate + 1.0) / baseline_weeks)
        if burst <= 0:
            continue
        lift = recent_score_sum[token] / count
        trends.append({
            "token": token,
            "burst": round(burst, 3),
            "recent_count": count,
            "baseline_count": base_count,
            "lift": round(lift, 3),
            "rank_score": burst * lift,
        })

    trends.sort(key=lambda t: -t["rank_score"])

    # "#rsmata" and "rsmata" are the same term and would otherwise burn two of the
    # limited slots; keep whichever variant scored higher.
    seen_stems: set[str] = set()
    deduped = []
    for trend in trends:
        stem = trend["token"].lstrip("#")
        if stem in seen_stems:
            continue
        seen_stems.add(stem)
        deduped.append(trend)
    top = deduped[:limit]
    logger.info(
        f"Detected {len(top)} rising token(s) from {len(recent_docs)} recent / "
        f"{len(baseline_docs)} baseline posts"
    )
    return top


def format_trends(trends: List[Dict[str, Any]]) -> str:
    """Render rising tokens for the prompt; empty string when there is nothing to say."""
    if not trends:
        return ""
    return ", ".join(f"{t['token']} ({t['recent_count']}x)" for t in trends)
