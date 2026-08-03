"""
Velocity derivation tasks (feature 006-longitudinal-metric-capture).

Turns an item's append-only observation series into rate of change. Deliberately
separate from `social_tasks.py`: nothing here performs platform I/O, so
Constitution II's collection retry carve-out does not apply and mixing the two
would blur that boundary.

Velocity is the system's closest available substitute for the retention and
watch-time signals it cannot obtain (Constitution VII), and it exists only
because observations accumulate. A single observation yields nothing — and that
is reported as "observed once", never as zero velocity.

Three invariants worth stating up front, because each one is a bug that would
pass a casual review:

  * Elapsed time is MEASURED between two timestamps, never inferred from an
    observation's position in the series (FR-013). Observations arrive on a
    ragged monthly cadence; treating them as evenly spaced silently rescales
    every rate.
  * A decrease is real (FR-012). Comments get deleted and view counters get
    revised. Clamping a negative delta to zero fabricates an observation.
  * An interval too short to divide by yields NO rate, not a huge one (FR-014).
    Its deltas are still stored — delta-present/rate-absent is a valid state.
"""
import logging
import os
from typing import Any, Dict, List, Optional

from prefect import task

try:
    from ..db import db_pool
except ImportError:
    import sys
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    from db import db_pool

logger = logging.getLogger(__name__)


async def _db_pool(db_env: Optional[str] = None):
    """The module-local pool seam every task module here exposes.

    Tests monkeypatch `_db_pool`, not `db_pool` — see the note in
    `availability_tasks.py` for why the seam is per-module.
    """
    return await db_pool(db_env)


# ---------------------------------------------------------------------------
# Configuration (contracts/velocity-derivation.md § Configuration)
# ---------------------------------------------------------------------------
# These are PROVISIONAL. They were chosen to be scale-free, not calibrated — no
# corpus of real series exists yet to calibrate against. `CONFIG_VERSION` is
# what makes recalibrating them safe: a later run under a new version rewrites
# its verdicts and says so, rather than silently reinterpreting earlier rows
# under thresholds they were never computed with (Constitution XII).

SECONDS_PER_DAY = 86400.0


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        logger.warning(f"{name} is not a number; falling back to {default}")
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        logger.warning(f"{name} is not an integer; falling back to {default}")
        return default


# Below this, no rate is produced. Every scheduled cadence here is monthly, so
# no legitimate interval falls under a day; the realistic sub-day pair is a
# manual re-run colliding with a scheduled one, where counts have barely moved
# and dividing by a fraction of a day inflates the rate wildly.
def min_interval_seconds() -> int:
    return _env_int("VELOCITY_MIN_INTERVAL_SECONDS", 86400)


# An interval is a plateau at or below this share of the item's OWN peak rate.
# Account baselines here differ ~8x, so an absolute threshold would fire
# constantly on large accounts and never on small ones.
def plateau_fraction() -> float:
    return _env_float("VELOCITY_PLATEAU_FRACTION", 0.10)


# Acceleration at or above this multiple of the preceding plateau's rate.
def acceleration_multiple() -> float:
    return _env_float("VELOCITY_ACCELERATION_MULTIPLE", 3.0)


# Plateau/acceleration read ONE metric. Measured on the live store, Instagram
# publishes `likes` on 791/793 rows but `views`/`comments` on only 338 (the
# Reels-enriched video subset). A per-metric verdict would emit acceleration
# flags for 57% of Instagram items resting on no data at all (research.md R3).
def detection_metric() -> str:
    metric = os.environ.get("VELOCITY_DETECTION_METRIC", "likes")
    if metric not in METRICS:
        logger.warning(f"VELOCITY_DETECTION_METRIC={metric!r} is not a stored metric; using 'likes'")
        return "likes"
    return metric


def config_version() -> str:
    return os.environ.get("VELOCITY_CONFIG_VERSION", "v1")


# The metric axis. Single definition — it is also the engagement column set on
# `harvested_signals` and `metric_observations`. Adding a metric and forgetting
# this list would derive velocity for it nowhere while reporting success.
METRICS = ("views", "likes", "comments", "shares")

# Closed vocabulary, mirrored by chk_velocity_rate_withheld in migration 008.
# Referenced by `derive_interval` rather than restated as a literal there, so a
# second reason added here is wired up rather than merely declared — the failure
# mode otherwise is a value that looks sanctioned and is rejected at write time.
INTERVAL_TOO_SHORT = "interval_too_short"
RATE_WITHHELD_REASONS = frozenset({INTERVAL_TOO_SHORT})


# ---------------------------------------------------------------------------
# Pure derivation (no I/O — unit-testable without a database)
# ---------------------------------------------------------------------------

def derive_interval(earlier: Dict[str, Any], later: Dict[str, Any],
                    min_seconds: Optional[int] = None) -> Dict[str, Any]:
    """
    Derive one interval from a consecutive pair of observations.

    `earlier`/`later` are mappings with `observed_at` (datetime) and the four
    metric keys. Returns deltas, measured elapsed time, and per-day rates.

    Elapsed time comes from SUBTRACTING TWO TIMESTAMPS. It is never inferred
    from the observations' positions in the series (FR-013) — the cadence here
    is monthly and ragged, so treating positions as uniform would silently
    rescale every rate by whatever the real spacing happened to be.

    A metric present in only one endpoint yields a NULL delta, not a delta
    against an assumed zero: "not published" and "was zero" are different facts
    and feature 005 exists to keep them apart.
    """
    if min_seconds is None:
        min_seconds = min_interval_seconds()

    elapsed = (later["observed_at"] - earlier["observed_at"]).total_seconds()
    elapsed_seconds = int(round(elapsed))

    deltas: Dict[str, Optional[int]] = {}
    for metric in METRICS:
        before, after = earlier.get(metric), later.get(metric)
        # A decrease is a real observation (FR-012). Comments get deleted and
        # view counters get revised; clamping to zero would fabricate a number
        # nobody observed.
        deltas[metric] = None if before is None or after is None else after - before

    rates: Dict[str, Optional[float]] = {m: None for m in METRICS}
    withheld_reason: Optional[str] = None
    if elapsed_seconds < min_seconds:
        # No rate at all — not a very large one. Dividing a near-zero elapsed
        # into a small delta produces a number that looks like an explosive
        # trend and is pure artefact. The deltas are still returned.
        withheld_reason = INTERVAL_TOO_SHORT
    else:
        days = elapsed_seconds / SECONDS_PER_DAY
        for metric, delta in deltas.items():
            if delta is not None:
                rates[metric] = delta / days

    return {
        "elapsed_seconds": elapsed_seconds,
        "deltas": deltas,
        "rates": rates,
        "rate_withheld_reason": withheld_reason,
    }


def detect_plateau_and_acceleration(
    intervals: List[Dict[str, Any]],
    metric: Optional[str] = None,
    plateau_frac: Optional[float] = None,
    accel_mult: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """
    Flag plateaus and late acceleration across one item's intervals, in order.

    Scale-free by construction: thresholds are relative to the ITEM'S OWN peak
    interval rate, never to an absolute count or an external baseline. Account
    baselines here differ by roughly 8x, so an absolute threshold would fire
    constantly on large accounts and never on small ones — the flag would end
    up measuring account size, which is exactly the failure feature 003's
    ranking rules document for flat engagement sums.

    Only RATED intervals participate. An interval whose rate was withheld is not
    a low rate — it is no rate — and treating it as a plateau would manufacture
    acceleration out of two same-day captures (FR-015a).

    Returns a list of `{"index", "is_plateau", "is_acceleration",
    "plateau_index"}`, indexed against the input list.
    """
    metric = metric or detection_metric()
    plateau_frac = plateau_fraction() if plateau_frac is None else plateau_frac
    accel_mult = acceleration_multiple() if accel_mult is None else accel_mult

    rate_key = f"rate_{metric}_per_day"
    verdicts = [
        {"index": i, "is_plateau": False, "is_acceleration": False, "plateau_index": None}
        for i in range(len(intervals))
    ]

    rated = [
        (i, iv[rate_key]) for i, iv in enumerate(intervals)
        if iv.get("rate_withheld_reason") is None and iv.get(rate_key) is not None
    ]
    # Two rated intervals minimum: the verdict is a COMPARISON between them, so
    # three observations are the floor. Fewer means no verdict — and the item is
    # NOT thereby recorded as non-accelerating (velocity_status says which).
    if len(rated) < 2:
        return verdicts

    peak = max(rate for _, rate in rated)
    if peak <= 0:
        # Every rated interval is flat or falling. "Plateau at <=10% of peak" is
        # meaningless against a non-positive peak, so no verdict is issued
        # rather than one derived from a sign flip.
        return verdicts

    for position, (index, rate) in enumerate(rated):
        if rate <= plateau_frac * peak:
            verdicts[index]["is_plateau"] = True
        if position == 0:
            continue
        prev_index, prev_rate = rated[position - 1]
        if verdicts[prev_index]["is_plateau"] and prev_rate > 0 and rate >= accel_mult * prev_rate:
            verdicts[index]["is_acceleration"] = True
            verdicts[index]["plateau_index"] = prev_index

    return verdicts


# ---------------------------------------------------------------------------
# Legacy seeding (FR-021)
# ---------------------------------------------------------------------------

async def _observation_backfill_impl(conn) -> Dict[str, Any]:
    """Seed one `legacy` observation per pre-feature signal row.

    Connection-level implementation so the flow can wrap it in a rollback for
    `--validate-only`, matching `spine_tasks._link_table_impl`.

    Takes NO `dry_run` flag, deliberately, and unlike `_link_table_impl`: the
    inserts must run in both modes so the reported count is what would actually
    land after the ON CONFLICT filter, not merely how many candidates matched.
    The caller owns the rollback via `maybe_transaction`.

    Two details that are the whole point of this function:

      * `observed_at` is the row's REAL `harvested_at` — the moment the value
        was written. Stamping it with now() would assert the value was observed
        at a time it was not, which Principle VI forbids outright. What that
        timestamp is NOT is a capture near publication, which is exactly why
        the row is marked `legacy` rather than `captured` (FR-023a).
      * Rows carrying no metric at all are skipped. They have nothing to
        observe, and an all-NULL observation would violate
        chk_metric_observations_has_a_value.

    Idempotent via NOT EXISTS: an item that already has any observation is left
    alone, so re-running after a harvest cannot inject a second legacy row.
    """
    # ONE anti-joined scan, partitioned in Python. Two queries differing only in
    # the polarity of the metric test would duplicate the NOT EXISTS predicate,
    # so a change to what "already observed" means would have to be made twice
    # or the two counts would stop describing the same population.
    unseeded = await conn.fetch(
        """
        SELECT s.platform, s.content_id, s.harvested_at, s.views, s.likes,
               s.comments, s.shares, s.account_id
        FROM harvested_signals s
        WHERE NOT EXISTS (
            SELECT 1 FROM metric_observations o
            WHERE o.platform = s.platform AND o.content_id = s.content_id)
        """
    )
    candidates = [r for r in unseeded if any(r[m] is not None for m in METRICS)]
    # Counted rather than silently dropped, so "nothing to do because everything
    # is seeded" stays distinguishable from "nothing to do because these rows
    # have nothing to observe".
    skipped_no_metrics = len(unseeded) - len(candidates)

    seeded = 0
    for row in candidates:
        inserted = await conn.fetchval(
            """
            INSERT INTO metric_observations
                (platform, content_id, observed_at, provenance,
                 views, likes, comments, shares, account_id)
            VALUES ($1, $2, $3, 'legacy', $4, $5, $6, $7, $8)
            ON CONFLICT (platform, content_id, observed_at) DO NOTHING
            RETURNING id
            """,
            row["platform"], row["content_id"], row["harvested_at"],
            row["views"], row["likes"], row["comments"], row["shares"],
            row["account_id"],
        )
        if inserted is not None:
            seeded += 1

    return {
        "candidates": len(candidates),
        "seeded": seeded,
        "skipped_no_metrics": skipped_no_metrics,
    }


# ---------------------------------------------------------------------------
# Derivation over the store
# ---------------------------------------------------------------------------

async def _derive_velocity_impl(
    conn,
    platform: Optional[str] = None,
    content_ids: Optional[List[str]] = None,
    since: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Derive every consecutive-pair interval for the items in scope.

    Idempotent by construction (FR-016c): an interval is keyed to its ordered
    observation pair, and observations are immutable, so re-running with
    unchanged config reproduces identical values. Re-running after a threshold
    change updates the verdict in place and rewrites `config_version` — the one
    place this system deliberately overwrites, and safe because a derived row is
    a function of two immutable endpoints plus configuration, never itself an
    observation.
    """
    # Fixed placeholders rather than a list built by interpolating $N from
    # len(args): that form is only correct while every branch appends exactly
    # one argument, so a filter binding two parameters — or a reordering —
    # misnumbers them silently. This keeps the SQL greppable as literal text.
    # `or None` preserves "an empty content_ids list means no filter".
    rows = await conn.fetch(
        """
        SELECT id, platform, content_id, observed_at, views, likes, comments, shares
        FROM metric_observations
        WHERE ($1::text   IS NULL OR platform = $1)
          AND ($2::text[] IS NULL OR content_id = ANY($2))
          AND ($3::text   IS NULL OR observed_at >= $3::timestamptz)
        ORDER BY platform, content_id, observed_at
        """,
        platform or None, content_ids or None, since or None,
    )

    series: Dict[tuple, List[Dict[str, Any]]] = {}
    for row in rows:
        series.setdefault((row["platform"], row["content_id"]), []).append(dict(row))

    metric = detection_metric()
    version = config_version()
    summary = {
        "items_considered": len(series),
        "intervals_derived": 0,
        "intervals_updated": 0,
        "intervals_withheld_too_short": 0,
        "accelerations_detected": 0,
        "items_insufficient_observations": 0,
        "config_version": version,
        "detection_metric": metric,
    }

    for (plat, content_id), observations in series.items():
        if len(observations) < 2:
            summary["items_insufficient_observations"] += 1
            continue

        derived: List[Dict[str, Any]] = []
        for earlier, later in zip(observations, observations[1:]):
            result = derive_interval(earlier, later)
            derived.append({
                "from_observation_id": earlier["id"],
                "to_observation_id": later["id"],
                "elapsed_seconds": result["elapsed_seconds"],
                "rate_withheld_reason": result["rate_withheld_reason"],
                **{f"delta_{m}": result["deltas"][m] for m in METRICS},
                **{f"rate_{m}_per_day": result["rates"][m] for m in METRICS},
            })

        verdicts = detect_plateau_and_acceleration(derived, metric=metric)

        # `is_plateau` rides along in the INSERT — it needs no interval id, and
        # a plateau is common (any interval at <=10% of the item's peak), so
        # writing it here rather than in the second pass removes most of that
        # pass's work. Only `is_acceleration` needs a second statement, because
        # it must point at the plateau's id, which does not exist until the
        # plateau row is written; chk_velocity_acceleration_has_plateau forbids
        # the flag without an antecedent, so that ordering is not optional.
        interval_ids: List[int] = []
        for record, verdict in zip(derived, verdicts):
            row = await conn.fetchrow(
                """
                INSERT INTO velocity_intervals
                    (platform, content_id, from_observation_id, to_observation_id,
                     elapsed_seconds, delta_views, delta_likes, delta_comments, delta_shares,
                     rate_views_per_day, rate_likes_per_day, rate_comments_per_day,
                     rate_shares_per_day, rate_withheld_reason, config_version,
                     is_plateau, derived_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16, now())
                ON CONFLICT (from_observation_id, to_observation_id) DO UPDATE SET
                    elapsed_seconds       = EXCLUDED.elapsed_seconds,
                    delta_views           = EXCLUDED.delta_views,
                    delta_likes           = EXCLUDED.delta_likes,
                    delta_comments        = EXCLUDED.delta_comments,
                    delta_shares          = EXCLUDED.delta_shares,
                    rate_views_per_day    = EXCLUDED.rate_views_per_day,
                    rate_likes_per_day    = EXCLUDED.rate_likes_per_day,
                    rate_comments_per_day = EXCLUDED.rate_comments_per_day,
                    rate_shares_per_day   = EXCLUDED.rate_shares_per_day,
                    rate_withheld_reason  = EXCLUDED.rate_withheld_reason,
                    config_version        = EXCLUDED.config_version,
                    derived_at            = now(),
                    is_plateau            = EXCLUDED.is_plateau,
                    -- Cleared here and re-set below only where it applies: a
                    -- stale verdict from an earlier config_version must not
                    -- survive a re-derivation.
                    is_acceleration       = false,
                    plateau_interval_id   = NULL
                RETURNING id, (xmax = 0) AS inserted
                """,
                plat, content_id, record["from_observation_id"], record["to_observation_id"],
                record["elapsed_seconds"],
                record["delta_views"], record["delta_likes"],
                record["delta_comments"], record["delta_shares"],
                record["rate_views_per_day"], record["rate_likes_per_day"],
                record["rate_comments_per_day"], record["rate_shares_per_day"],
                record["rate_withheld_reason"], version, verdict["is_plateau"],
            )
            interval_ids.append(row["id"])
            # `xmax = 0` is true only for a fresh INSERT, so a re-run reports
            # "updated" instead of inflating "derived" — which is what makes the
            # idempotence claim checkable from the summary alone.
            if row["inserted"]:
                summary["intervals_derived"] += 1
            else:
                summary["intervals_updated"] += 1
            if record["rate_withheld_reason"]:
                summary["intervals_withheld_too_short"] += 1

        for verdict in verdicts:
            if not verdict["is_acceleration"]:
                continue
            await conn.execute(
                "UPDATE velocity_intervals SET is_acceleration = true, "
                "plateau_interval_id = $2 WHERE id = $1",
                interval_ids[verdict["index"]], interval_ids[verdict["plateau_index"]],
            )
            if verdict["is_acceleration"]:
                summary["accelerations_detected"] += 1

    return summary


@task(name="velocity.interval.derive", retries=2, retry_delay_seconds=10)
async def velocity_interval_derive(
    platform: Optional[str] = None,
    content_ids: Optional[List[str]] = None,
    since: Optional[str] = None,
    db_env: Optional[str] = None,
) -> Dict[str, Any]:
    """Task wrapper around `_derive_velocity_impl` for standalone use."""
    pool = await _db_pool(db_env)
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                return await _derive_velocity_impl(
                    conn, platform=platform, content_ids=content_ids, since=since
                )
    finally:
        await pool.close()


@task(name="velocity.observation.backfill", retries=2, retry_delay_seconds=10)
async def velocity_observation_backfill(db_env: Optional[str] = None) -> Dict[str, Any]:
    """Task wrapper around `_observation_backfill_impl` for standalone use."""
    pool = await _db_pool(db_env)
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                return await _observation_backfill_impl(conn)
    finally:
        await pool.close()
