# Contract: Velocity Derivation

**Flows**: `velocity-derive` (scheduled monthly), `observation-backfill` (one-time, unscheduled)

**Tasks**: `velocity.interval.derive`, `velocity.acceleration.detect`, `velocity.observation.backfill`

**Feature**: 006-longitudinal-metric-capture

The one-time legacy seeding lives in its own flow, mirroring `flows/spine_backfill.py`. It is
deliberately **not** a parameter on `velocity-derive`: that flow is scheduled monthly and must not
carry a one-shot operation that would fire on every run if the default were ever wrong.

Reads immutable observations, writes derived intervals. Performs no platform I/O and no model calls.

## Flow

```python
@flow(name="velocity-derive")
async def velocity_derive_flow(
    platform: Optional[str] = None,      # restrict scope; None = all
    content_ids: Optional[List[str]] = None,
    since: Optional[str] = None,         # only items observed since this date
    validate_only: bool = False,         # FR-027 dry run
) -> Dict[str, Any]                      # standard {start_time, end_time, data, summary, error}
```

Never raises (Constitution I). CLI: `docker exec prefect python flows/velocity_derive.py [--validate-only]`.

Summary reports: `items_considered`, `intervals_derived`, `intervals_updated`,
`intervals_withheld_too_short`, `accelerations_detected`, `items_insufficient_observations`,
`config_version`.

`--validate-only` reports every one of those without writing (Constitution's costed-batch rule,
FR-027). Model spend is always zero, so the dry run's purpose is volume and blast radius, not cost.

## Derivation

For each item, load observations ordered by `observed_at`, then for each consecutive pair:

| Field | Rule |
|---|---|
| `elapsed_seconds` | `o₂.observed_at − o₁.observed_at`, measured. Never assumed, never indexed by position (FR-013) |
| `delta_m` | `o₂.m − o₁.m`; NULL if either endpoint is NULL. **Negative values stored as-is** (FR-012) |
| `rate_m_per_day` | `delta_m ÷ (elapsed_seconds ÷ 86400)` when `elapsed_seconds ≥ MIN_INTERVAL_SECONDS` (default 86400); otherwise NULL |
| `rate_withheld_reason` | `'interval_too_short'` when the floor was not met, else NULL |

**Deltas are stored even when the rate is withheld.** Delta-present/rate-absent is a valid state
(FR-014).

**`elapsed_seconds` must be > 0.** Equal timestamps cannot occur — `uq_metric_observation_capture`
forbids two observations of one item at the same instant — so a zero elapsed is a data defect and
the CHECK should fail loudly rather than be defended against with a guard clause.

### Upsert (FR-016c)

Keyed on `(from_observation_id, to_observation_id)`. Re-running with unchanged config reproduces
identical rows; re-running after a threshold change updates the verdict in place and rewrites
`config_version`. No interval is ever duplicated.

## Plateau and acceleration

Scale-free, against the item's own rated intervals only (FR-015, FR-015a):

```
rated     = intervals with a non-NULL rate, in time order      # withheld ones excluded entirely
peak      = max(rate over rated)
is_plateau      ⟺ rate ≤ PLATEAU_FRACTION × peak                    # default 0.10
is_acceleration ⟺ predecessor in `rated` is_plateau
                  AND rate ≥ ACCELERATION_MULTIPLE × predecessor.rate  # default 3.0
plateau_interval_id = predecessor.id on an acceleration row
```

Needs ≥3 observations (2 rated intervals). Fewer ⇒ no verdict, and the item is **not** recorded as
non-accelerating — absence of a flag is not evidence (`velocity_status` says which).

**Detection metric**: `likes`, per [research.md R3](../research.md) — the only metric with
near-universal coverage (791/793, versus 338 for views and comments). A per-metric verdict would
produce acceleration flags for 57% of Instagram items that rest on no data. Configuration, not a
constant.

**Withheld intervals are excluded, not treated as plateaus.** An interval with no rate is not a low
rate; counting it as a plateau would manufacture acceleration out of two same-day captures.

## Configuration

| Name | Default | Meaning |
|---|---|---|
| `VELOCITY_MIN_INTERVAL_SECONDS` | `86400` | Floor below which no rate is produced |
| `VELOCITY_PLATEAU_FRACTION` | `0.10` | Plateau at ≤ this share of the item's peak rate |
| `VELOCITY_ACCELERATION_MULTIPLE` | `3.0` | Acceleration at ≥ this multiple of the plateau rate |
| `VELOCITY_DETECTION_METRIC` | `likes` | Metric the plateau/acceleration verdict reads |
| `VELOCITY_CONFIG_VERSION` | `v1` | Stamped onto every derived row |

**These defaults are provisional.** They were chosen to be scale-free, not calibrated — no corpus of
real series exists yet. `config_version` is what makes recalibration safe: a later run under `v2`
rewrites verdicts and says so, rather than silently reinterpreting `v1` rows (Principle XII).

## Deployment

`prefect.yaml` entry `velocity-derive`, monthly, scheduled **after** the harvest window closes. The
per-client harvests run 17:30 on the 1st through 02:00 on the 2nd, and competitor harvests
02:30–05:00 on the 2nd; derivation therefore schedules at **06:00 WIB on the 2nd**, clear of both.

A derivation that runs early is harmless — it derives what exists and the next run picks up the
rest, which `velocity_status.not_yet_derived` makes visible.

## Testing

Real disposable Postgres, `pytestmark = pytest.mark.schema`, per the knowledge-base precedent:

- Irregular spacing: observations at +2d and +26d yield per-day rates reflecting each interval's own
  elapsed time — and differ from what uniform spacing would give (SC-007).
- Decrease: a lower later count stores a negative delta, unclamped.
- Sub-24h pair: deltas stored, all rates NULL, reason `interval_too_short`.
- Idempotence: two consecutive runs leave row count and values identical.
- Threshold change: re-run under a new `config_version` updates in place, no duplicates.
- Single observation: no interval, and the item is not recorded as zero-velocity.
- Legacy endpoint: an interval anchored on a `legacy` observation is derivable and identifiable
  as such (FR-023a).
- Scale-free: identical trajectories on accounts differing by an order of magnitude flag identically.

Pure arithmetic is unit-tested without a database.
