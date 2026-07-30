"""
Songbird theme allocation — how much to vary vs how much to double down.

Answers a strategy question the generator previously had no opinion on: given a month
of N content slots, how many should repeat a theme that demonstrably works, and how
many should try something untested?

Modelled as a **batched multi-armed bandit** over themes (Thompson sampling): each
theme's observed exemplar scores form a posterior, and slots are allocated by sampling
from those posteriors. The behaviour that falls out is the answer:

| Evidence state                        | Effect                          |
|---------------------------------------|---------------------------------|
| Few posts per theme (wide posteriors) | draws overlap -> slots spread   |
| One theme clearly ahead, many posts   | narrow posterior -> it repeats  |
| New or trending theme (no history)    | seeded uncertain -> always tried |

Two guard rails come from the content-fatigue literature, which finds saturation is
driven less by volume than by *diminishing distinctiveness*: no theme may take more
than MAX_THEME_SHARE of a month, and a floor of slots is reserved for untested themes.
When nothing has enough evidence, it degrades to the conventional 70/20/10 split.

`allocate_slots` is pure and deterministic under a seed, so it is unit-testable;
theme *induction* needs an LLM and lives in `songbird_theme_induction`.
"""
from __future__ import annotations

import json
import logging
import math
import os
import random
from typing import Any, Dict, List, Optional

from prefect import task

try:
    from .openrouter_tasks import array_schema, openrouter_chat
except ImportError:  # standalone execution
    import sys

    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    from tasks.openrouter_tasks import array_schema, openrouter_chat

logger = logging.getLogger(__name__)

# No single theme may own more than this share of a month — distinctiveness, not
# volume, is what audiences fatigue on.
MAX_THEME_SHARE = 0.40

# Minimum share of slots reserved for themes with thin evidence, so a locally strong
# theme cannot freeze out discovery entirely.
MIN_EXPLORE_SHARE = 0.15

# A theme needs this many observed posts before its mean is treated as evidence.
MIN_OBSERVATIONS = 3

# Cold-start split when no theme clears MIN_OBSERVATIONS (the 70/20/10 convention:
# proven / adjacent / experimental).
COLD_START_SPLIT = (0.70, 0.20, 0.10)

# Prior for an unobserved theme: neutral mean, wide spread (guarantees exploration).
PRIOR_MEAN = 0.5
PRIOR_SPREAD = 0.25


def _posterior(scores: List[float]) -> tuple[float, float]:
    """
    Mean and spread of a theme's performance posterior.

    Scores are already 0-1 percentiles from the ranking module, so a Beta-like spread
    `sqrt(mu(1-mu)/(n+1))` is the natural uncertainty: it shrinks as evidence
    accumulates and stays wide for themes we have barely seen.
    """
    if not scores:
        return PRIOR_MEAN, PRIOR_SPREAD
    n = len(scores)
    mean = sum(scores) / n
    spread = math.sqrt(max(mean * (1 - mean), 0.01) / (n + 1))
    return mean, max(spread, 0.02)


def allocate_slots(
    themes: List[Dict[str, Any]],
    total: int,
    *,
    seed: Optional[int] = None,
    max_share: float = MAX_THEME_SHARE,
    min_explore_share: float = MIN_EXPLORE_SHARE,
) -> List[Dict[str, Any]]:
    """
    Assign each of `total` content slots to a theme, with an explore/exploit intent.

    Args:
        themes: [{"theme": str, "scores": [float, ...]}, ...] — `scores` are the
            ranking percentiles of the exemplars assigned to that theme.
        total: Number of content slots to fill.
        seed: Makes sampling reproducible (a plan should be explicable after the fact).

    Returns:
        [{"theme", "intent": "exploit"|"explore", "evidence": n}, ...], length `total`.
        `intent` drives the prompt: exploit slots adapt a proven pattern with a NEW
        angle, explore slots try an untested one.
    """
    if total <= 0:
        return []
    if not themes:
        return [{"theme": None, "intent": "explore", "evidence": 0} for _ in range(total)]

    rng = random.Random(seed)
    stats = {}
    for entry in themes:
        name = entry.get("theme")
        scores = [float(s) for s in (entry.get("scores") or [])]
        mean, spread = _posterior(scores)
        stats[name] = {"mean": mean, "spread": spread, "n": len(scores)}

    proven = [n for n, s in stats.items() if s["n"] >= MIN_OBSERVATIONS]
    # The fatigue cap can only be honoured when there are enough themes to spread
    # across: with 2 themes and 10 slots, a 40% cap leaves 2 slots unfillable. Never
    # let it fall below an even split, or the allocation silently overflows it.
    even_split = math.ceil(total / len(stats))
    cap = max(1, even_split, int(math.floor(total * max_share)))
    explore_floor = math.ceil(total * min_explore_share)
    untested = [n for n, s in stats.items() if s["n"] < MIN_OBSERVATIONS]

    # Cold start: nothing has enough history to sample meaningfully, so fall back to
    # the conventional proven/adjacent/experimental split across whatever we have.
    if not proven:
        names = list(stats) or [None]
        allocation = []
        for index in range(total):
            allocation.append({
                "theme": names[index % len(names)],
                "intent": "explore",
                "evidence": stats.get(names[index % len(names)], {}).get("n", 0),
            })
        logger.info(
            f"Theme allocation cold start: no theme has >={MIN_OBSERVATIONS} observations; "
            f"spreading {total} slots across {len(names)} theme(s) (split {COLD_START_SPLIT})"
        )
        return allocation

    allocation: List[Dict[str, Any]] = []
    used: Dict[str, int] = {}
    explore_used = 0
    for index in range(total):
        remaining = total - index
        # Honour the exploration floor near the end of the month rather than letting a
        # strong theme consume every remaining slot.
        force_explore = untested and (explore_floor - explore_used) >= remaining

        eligible = [n for n in stats if used.get(n, 0) < cap]
        if force_explore:
            candidates = [n for n in eligible if n in untested] or eligible
        else:
            candidates = eligible
        if not candidates:  # every theme hit its cap — relax rather than under-fill
            candidates = list(stats)

        draws = {
            name: rng.gauss(stats[name]["mean"], stats[name]["spread"]) for name in candidates
        }
        winner = max(draws, key=draws.get)
        evidence = stats[winner]["n"]
        intent = "exploit" if evidence >= MIN_OBSERVATIONS else "explore"
        if intent == "explore":
            explore_used += 1
        used[winner] = used.get(winner, 0) + 1
        allocation.append({"theme": winner, "intent": intent, "evidence": evidence})

    counts = {}
    for slot in allocation:
        counts[slot["theme"]] = counts.get(slot["theme"], 0) + 1
    logger.info(
        f"Theme allocation over {total} slots (cap {cap}/theme): {counts}; "
        f"{sum(1 for a in allocation if a['intent'] == 'exploit')} exploit / "
        f"{sum(1 for a in allocation if a['intent'] == 'explore')} explore"
    )
    return allocation


# The shared schema helpers build objects of string keys only, so indexes come back as
# a comma-separated string ("0,3,7") rather than a JSON array. Cheaper than adding a
# second schema builder for one caller.
THEME_SCHEMA = array_schema("content_themes", ["theme", "exemplar_indexes"])


def _parse_indexes(raw: Any, upper: int) -> List[int]:
    """Parse '0, 3, 7' into in-range indexes, ignoring anything malformed."""
    indexes = []
    for part in str(raw or "").replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) < upper:
            indexes.append(int(part))
    return list(dict.fromkeys(indexes))


@task(name="songbird.theme.induce", retries=1, retry_delay_seconds=20)
async def songbird_theme_induction(
    performers: List[Dict[str, Any]], max_themes: int = 8, client: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Group scored exemplars into content themes so performance can be measured per theme.

    Induced from the harvested content itself rather than from the knowledge base: KB
    `subject` values are business facts ("Operational Hours", "Services Offered"), not
    content themes. Uses one small structured LLM call, which handles Indonesian
    free text far better than keyword clustering and needs no new dependency.

    Best-effort — returns [] on any failure so the caller falls back to configured
    content pillars, then to uniform allocation (FR-011 degradation).

    `client` is an attribution label only; it rides into the token-usage log so
    theme-induction spend can be split per client.

    Returns:
        [{"theme": str, "scores": [float, ...], "examples": [str, ...]}, ...]
    """
    usable = [p for p in performers if (p.get("summary") or p.get("caption"))]
    if not usable:
        return []

    listing = "\n".join(
        f"{i}. {' '.join(str(p.get('summary') or p.get('caption') or '').split())[:200]}"
        for i, p in enumerate(usable)
    )
    try:
        result = await openrouter_chat(
            system=(
                "Anda adalah content strategist. Kelompokkan konten ke dalam tema besar "
                "berbahasa Indonesia yang ringkas (2-4 kata per tema). Setiap konten masuk "
                "ke TEPAT satu tema."
            ),
            user=(
                f"Kelompokkan {len(usable)} konten berikut menjadi maksimal {max_themes} tema.\n"
                f"Balas dengan daftar tema beserta index kontennya.\n\n{listing}"
            ),
            response_format=THEME_SCHEMA,
            max_tokens=1500,
            call_site="songbird.theme.induce",
            client=client,
        )
    except Exception as exc:
        logger.warning(f"Theme induction failed ({exc}); falling back to configured pillars")
        return []

    raw_themes = result.get("items", []) if isinstance(result, dict) else []
    themes: List[Dict[str, Any]] = []
    for entry in raw_themes:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("theme") or "").strip()
        if not name:
            continue
        indexes = _parse_indexes(entry.get("exemplar_indexes"), len(usable))
        if not indexes:
            continue
        themes.append({
            "theme": name,
            "scores": [float(usable[i].get("score") or 0.5) for i in indexes],
            "examples": [str(usable[i].get("profile_key") or "") for i in indexes][:3],
        })

    logger.info(f"Induced {len(themes)} themes from {len(usable)} exemplars")
    return themes


def themes_from_pillars(pillars: List[str]) -> List[Dict[str, Any]]:
    """Fallback arms when induction is unavailable: the client's configured pillars."""
    return [{"theme": p, "scores": [], "examples": []} for p in pillars if str(p).strip()]
