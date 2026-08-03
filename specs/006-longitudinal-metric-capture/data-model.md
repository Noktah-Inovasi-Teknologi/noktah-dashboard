# Data Model: Longitudinal Metric Capture

**Feature**: 006-longitudinal-metric-capture | **Migration**: `008_observation_history`

Two new tables, one view, one widened vocabulary. Everything else is untouched.

```
harvested_signals ──1:N──> metric_observations ──pairs──> velocity_intervals
      (latest value)         (append-only truth)          (derived, re-runnable)
                                     │
                              velocity_status (view: exactly one reason per item)

capture_outcomes  ── unchanged shape, one new capture_kind ── records misses
```

---

## `metric_observations` — append-only

One timestamped capture of a content item's public counts. Many rows per item. **Never updated,
never deleted.**

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | BIGSERIAL | no | PK |
| `platform` | TEXT | no | CHECK `IN ('instagram','tiktok')` |
| `content_id` | TEXT | no | |
| `observed_at` | TIMESTAMPTZ | no | When the value was seen. Not defaulted for legacy rows — see below |
| `provenance` | TEXT | no | CHECK `IN ('captured','legacy')` |
| `views` | BIGINT | yes | |
| `likes` | BIGINT | yes | |
| `comments` | BIGINT | yes | |
| `shares` | BIGINT | yes | |
| `account_id` | UUID | yes | FK → `accounts(id)` |
| `run_id` | UUID | yes | FK → `runs(id)` |

**Keyed by `(platform, content_id)`, not `harvested_signals.id`** — the same decision, for the same
reason, as `capture_outcomes` in feature 005: an observation must be recordable independently of
whether a signal row exists, and the identity of a content item is the platform pair, not a
surrogate key in a table that may be purged by the failure-retry path.

**Nullable metrics are load-bearing.** A NULL means "not published / not captured", resolved by
`field_availability` and `capture_outcomes` exactly as for `harvested_signals`. A zero is a real
observation. Per R3, `views`/`comments` will be NULL on roughly 57% of Instagram rows and `shares`
on all of them — this is expected, not a defect.

### Constraints

- `chk_metric_observations_platform` — CHECK `platform IN ('instagram','tiktok')`
- `chk_metric_observations_provenance` — CHECK `provenance IN ('captured','legacy')`
- `chk_metric_observations_has_a_value` — CHECK at least one of `views/likes/comments/shares` is
  NOT NULL. An observation recording nothing is not an observation; the correct record for "the
  capture produced nothing" is a `capture_outcomes` row, not an empty observation.
- `uq_metric_observation_capture` — UNIQUE `(platform, content_id, observed_at)`. Makes the harvest
  pass safely retryable: a re-run at the same instant cannot double-insert. Distinct instants
  remain distinct rows, which is what allows two runs in one day (FR-014 then withholds the rate).

### Indexes

- `ix_metric_observations_item` on `(platform, content_id, observed_at)` — the series read; also
  serves the "latest observation" lookup and the consecutive-pair walk.
- `ix_metric_observations_run` on `(run_id)`
- `ix_metric_observations_account` on `(account_id, observed_at DESC)`

### Invariants

1. No `UPDATE` or `DELETE` ever issues against this table in application code (FR-007).
2. Exactly one `legacy` row exists per pre-feature content item; none is ever created afterwards.
3. An item "lacks observation history" **iff** its only row has `provenance = 'legacy'` (FR-021a).
   This is derived, never stored.

---

## `velocity_intervals` — derived, recomputed in place

One row per consecutive pair of observations of an item. Not an observation; see the plan's
Complexity Tracking for why overwriting these does not breach Principle VII.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | BIGSERIAL | no | PK |
| `platform` | TEXT | no | |
| `content_id` | TEXT | no | |
| `from_observation_id` | BIGINT | no | FK → `metric_observations(id)` |
| `to_observation_id` | BIGINT | no | FK → `metric_observations(id)` |
| `elapsed_seconds` | BIGINT | no | Measured, not assumed. CHECK `> 0` |
| `delta_views` | BIGINT | yes | May be negative (FR-012) |
| `delta_likes` | BIGINT | yes | May be negative |
| `delta_comments` | BIGINT | yes | May be negative |
| `delta_shares` | BIGINT | yes | May be negative |
| `rate_views_per_day` | DOUBLE PRECISION | yes | NULL when withheld or metric absent |
| `rate_likes_per_day` | DOUBLE PRECISION | yes | |
| `rate_comments_per_day` | DOUBLE PRECISION | yes | |
| `rate_shares_per_day` | DOUBLE PRECISION | yes | |
| `rate_withheld_reason` | TEXT | yes | CHECK `IN ('interval_too_short')` when set |
| `is_plateau` | BOOLEAN | no | default false |
| `is_acceleration` | BOOLEAN | no | default false |
| `plateau_interval_id` | BIGINT | yes | FK → `velocity_intervals(id)`; set on an acceleration row |
| `derived_at` | TIMESTAMPTZ | no | default `now()` |
| `config_version` | TEXT | no | Thresholds in force when derived |

**`uq_velocity_interval_pair`** — UNIQUE `(from_observation_id, to_observation_id)`. This is what
makes re-derivation idempotent (FR-016c): the upsert targets this key.

**A delta and its rate are independent.** An interval under the 24-hour minimum stores its deltas
and leaves every rate NULL with `rate_withheld_reason = 'interval_too_short'` (FR-014). Delta
present + rate absent is a valid, meaningful state.

**`config_version`** records which thresholds produced the verdict. The defaults are provisional
(24h / 10% / 3×) and will be recalibrated once a corpus exists; without this column a re-derivation
would silently reinterpret earlier rows, which Principle XII forbids.

### Derivation rules

Given observations `o₁ … oₙ` for an item, ordered by `observed_at`, for each consecutive pair:

```
elapsed_seconds = o₂.observed_at − o₁.observed_at
delta_m         = o₂.m − o₁.m                        for each metric m, NULL if either side NULL
rate_m_per_day  = delta_m / (elapsed_seconds / 86400)  iff elapsed_seconds ≥ 86400
                  otherwise NULL, rate_withheld_reason = 'interval_too_short'
```

Plateau and acceleration, scale-free against the item's own intervals (FR-015), computed over
intervals that have a rate — withheld intervals do not participate (FR-015a):

```
peak      = max(rate_likes_per_day) over the item's rated intervals    # primary metric, see below
is_plateau      ⟺ rate ≤ PLATEAU_FRACTION × peak                       # default 0.10
is_acceleration ⟺ the immediately preceding rated interval is_plateau
                  AND rate ≥ ACCELERATION_MULTIPLE × that plateau rate # default 3.0
```

Requires ≥3 observations, since it compares two intervals.

**Detection runs on `likes`**, because R3 measured it as the only metric with near-universal
coverage (791/793 vs 338). Running detection per metric would produce an acceleration verdict for
57% of Instagram items that rests on no data at all. The chosen metric is configuration, not a
constant, so it can change if coverage changes.

---

## `velocity_status` — view, one reason per item

Answers FR-024. Mirrors feature 005's why-empty query shape: a single `CASE`, ordered so exactly one
branch can match, with a terminal `UNRESOLVED` branch that must always return zero rows.

Columns: `platform`, `content_id`, `observation_count`, `has_velocity`, `reason`.

Reason vocabulary, in evaluation order:

| Reason | Means |
|---|---|
| `has_velocity` | ≥1 rated interval exists |
| `legacy_only` | Single observation, `provenance = 'legacy'` — predates capture (FR-021a) |
| `observed_once` | Single observation, captured by this feature; needs another cycle |
| `not_yet_derived` | ≥2 observations but no interval row — derivation has not run |
| `all_intervals_too_short` | Intervals exist, every one withheld under the 24h floor |
| `no_longer_observable` | Latest `capture_outcomes` row says `aged_out_of_listing` or `deleted` |
| `UNRESOLVED` | **Always zero rows.** A non-zero count is a defect, asserted by test |

`observation_count` satisfies FR-017 — sample size travels with the value, per Principle VIII.

---

## `capture_outcomes` — extended vocabulary, unchanged shape

No structural change. One `capture_kind` value added:

```
capture_kind += 'metric_refresh'
```

Recorded per FR-018/FR-019:

| Situation | `outcome` | `reason` |
|---|---|---|
| Counts observed and stored | `success` | NULL |
| Absent, published **before** the oldest item the listing returned — provably beyond reach | `not_attempted` | `aged_out_of_listing` |
| Absent, published **after** the oldest returned item — should have been listed and wasn't | `not_attempted` | `absent_within_reach` |
| Platform explicitly reported the content gone (download path only) | `not_attempted` | `deleted` |
| Listing itself failed (block, throttle, parse) | `failed` | existing vocabulary — `blocked`, `timeout`, `parse_failure`, … |
| Item listed but carried no usable counts | `no_match` | NULL |

**`not_attempted` needs no reason-vocabulary change**: `chk_capture_outcomes_reason` constrains
reasons only when `outcome = 'failed'`, so the aged-out and deleted reasons are storable without
loosening the rule that a genuine failure must carry a classified reason. Only the `capture_kind`
CHECK is replaced, with a strict superset (plan → Complexity Tracking).

**`aged_out_of_listing` is not a failure.** Collapsing it into `failed` would restore exactly the
ambiguity feature 005 removed, and would make 252 measured rows (R2) look like a collection
regression.

**`absent_within_reach` is not `deleted`.** The discriminator is the `published_at` of the oldest
item the listing actually returned — not a rank against `list_depth`, which breaks when an account
has fewer than 30 posts or the listing returns a short page. An item published after that boundary
should have been in the response; its absence is *consistent with* removal, but a short page or a
listing hiccup produces the same observation. Recording it as `deleted` would assert something never
observed, which Principle VI forbids. `deleted` is reserved for an explicit platform not-found,
which only the download path can produce.

---

## `harvested_signals` — unchanged structurally

Retained as the latest-value convenience (FR-010). The refresh path updates **only** metric columns
and `harvested_at`. It MUST NOT write `subtitle`, `content_flow`, `summary`, or `advertisement`
(FR-010a) — see R8 for the destruction this prevents.

---

## Migration and parity

`008_observation_history.sql` follows the contract in `.claude/rules/backend/schema.md`: idempotent
(`IF NOT EXISTS`, `pg_constraint` guards), transactional, self-recording into `schema_migrations`,
with a real `.down.sql` that drops both new tables and the view and restores the original
`capture_kind` CHECK.

Both new tables hold only data this feature creates, so the reversal is genuinely safe — unlike the
001–003 baselines whose down-scripts are deliberate no-ops.

`init.sql` must be updated in the same commit, with constraints declared as **named ALTERs matching
the migration exactly**, per the parity trap documented in the schema rules. Verified by
`script/verify_schema_parity.sh`.
