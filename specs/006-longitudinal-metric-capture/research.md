# Phase 0 Research: Longitudinal Metric Capture

**Feature**: 006-longitudinal-metric-capture | **Date**: 2026-08-03

All figures below were measured against the live `noktah_dashboard` database on 2026-08-03, not
estimated. Queries are reproducible from [quickstart.md](./quickstart.md).

---

## R1 — How much pre-existing content is there, really?

**Decision**: Define the pre-existing set as "every `harvested_signals` row present when migration
008 runs", not as the literal count 656. Record the actual count at migration time as the seeding
task's reported output.

**Measured**:

```
total_signals | profiles | first_harvest | last_harvest
          819 |       22 |    2026-07-15 |   2026-08-01
```

**Rationale**: The spec inherited 656 from the audit's late-July snapshot; feature 005's plan
recorded 696 a day later; it is 819 today and still climbing, because monthly harvests keep adding
rows. An acceptance criterion pinned to a literal count would fail for a reason that has nothing to
do with correctness.

**Consequence for the spec**: SC-008 and FR-021 reference "the 656 pre-existing content items". The
substance is right and the number is stale. Read those as "all pre-feature rows"; the seeding task
reports the true count when it runs. Worth correcting in the spec text before implementation, but it
does not change any design decision here.

**Alternatives considered**: Freezing the count at migration time into a config value — rejected as
a redundant second source of truth for something a `COUNT(*)` answers exactly.

---

## R2 — How much of the corpus can actually be re-observed for free?

> ## ⚠️ CORRECTED 2026-08-03, after a live run
>
> **The figures below are wrong, and the error made the feature look worse than it is.**
>
> This analysis assumed `list_depth = 30`. That is the default on the `run_harvest`
> *function signature*, but every `harvest-monthly-*` deployment runs
> `social_harvest_window.py`, whose `DEFAULT_LIST_DEPTH = 150`, and none of them overrides it.
>
> Measured live against `lasikasyik`: one listing returned **182 items** (Instagram merges the
> posts and reels tabs, so the count can exceed the requested depth). No account in the corpus
> has more than **69** harvested items.
>
> **Corrected: all 819 rows are reachable. Zero are permanently frozen.**
>
> | | at depth 30 (assumed) | at depth 150 (actual) |
> |---|---|---|
> | Reachable | 567 | **819** |
> | Permanently frozen | 252 | **0** |
>
> The design is unaffected — `aged_out_of_listing` is still the right classification for an item
> that does eventually fall out of reach, and the reach ceiling is still real, just far higher
> than stated. What changes is the expected outcome: essentially the whole corpus should gain a
> second observation on the next monthly cycle, not 69% of it.
>
> The rest of this section is retained as written, for the record.

**Decision**: Accept that re-observability is bounded by listing residency, quantify it, and record
the unreachable remainder as a classified reason (`aged_out_of_listing`) rather than as an ordinary
missing value.

**Measured** — rank of each item within its own account by publication date, against the default
`list_depth` of 30:

```
within_depth30 | beyond_depth30 | pct_reobservable
           567 |            252 |             69.2
```

Posting rate per account over the last 30 days, which governs how fast an item ages out:

```
posts_last_30d : accounts
             2 : 1      15 : 1      21 : 1
             5 : 1      16 : 2      29 : 1
             7 : 3      17 : 5      66 : 1
             8 : 2      18 : 2
             9 : 1
            10 : 1
```

**Rationale**: Twelve of thirteen actively-posting accounts publish at or under 29 items/month, so a
listing depth of 30 spans roughly a month or more of their output and an item typically remains
observable across two or three monthly runs — enough for the two intervals that acceleration
detection requires (FR-015). The single account at 66/month loses an item from the listing in about
two weeks, so its content will rarely accumulate three observations. Nine profiles have no recent
posts at all, so their entire back-catalogue stays within depth and remains observable indefinitely.

**The 252 already-frozen rows are unrecoverable within this feature's scope.** They aged past depth
before longitudinal capture existed. Reaching them means listing deeper, which is collection
expansion (S-02) and out of scope. They are not defects and must not be reported as capture
failures.

**Alternatives considered**:
- *Raise `list_depth` for the monthly deployments* — rejected: directly increases request volume,
  violating FR-026 and Constitution X, and is explicitly S-02.
- *A separate refresh-only listing pass* — rejected for the same reason; it is new collection.
- *Silently ignoring the aged-out set* — rejected under Constitution V/VII: an item that can never
  be re-observed must say so, or it is indistinguishable from one that simply has not been observed
  twice yet.

---

## R3 — Which metrics will actually yield velocity?

**Decision**: Store all four metrics per observation, derive velocity per metric independently, and
require consumers to read the per-metric observation count (FR-017) rather than assume uniform
coverage.

**Measured**:

```
platform  | rows | has_views | has_likes | has_comments | has_shares
instagram |  793 |       338 |       791 |          338 |          0
tiktok    |   26 |        26 |        26 |           26 |          0
```

**Rationale**: Instagram publishes `likes` almost universally here (791/793) but `views` and
`comments` only on the Reels-enriched video subset (338, 43%). Share counts are absent across the
whole corpus — Instagram publishes none at all, and the 26 TikTok rows predate the `shares` column
being populated. So likes-velocity will be dense and comment/view-velocity will exist for well under
half the corpus, while share-velocity will not exist at all initially.

This is a coverage fact, not a bug, and feature 005 already recorded *why* each is absent in
`field_availability`. Velocity inherits that: a metric with no observations produces no interval,
which is distinct from an interval whose delta is zero.

**Alternatives considered**: Deriving a single blended engagement velocity — rejected. It would mask
which underlying metric moved, and blending a dense metric with a sparse one produces a series whose
composition changes silently as coverage changes. Feature 003's ranking rules already document the
same failure mode for flat `likes + comments` sums.

---

## R4 — Where does the refresh hook into the harvest?

**Decision**: A single observation pass over `all_items`, placed after per-profile account
resolution and before the per-item download loop. The de-duplication gate is left untouched.

**Evidence** — [social_harvest.py:510-511](../../service/prefect/flows/common/social_harvest.py#L510-L511):

```python
all_items = listing.get("items", [])
items = depth_selector(all_items)
```

The depth selector is a pure client-side filter (`make_time_window_selector`,
`make_recent_n_selector`) applied to an already-fetched response. Every item in `all_items` carries
its `public_counts` at this point; items dropped by the filter have their current counts discarded.

**Rationale**: Observing `all_items` rather than `items` costs nothing extra and is the only way the
31-day window deployments can build a series on content older than 31 days. Placing the pass before
the download loop means it runs even for items the gate will skip — which is the entire point — and
placing it after account resolution means `account_id` is available for the observation row.

**Alternatives considered**:
- *Modify the dedup gate to fall through* — rejected: it would run the observation inside the
  per-item loop, which only iterates the depth-selected subset, so the same-window-only limitation
  would remain.
- *Observe at `social_signal_record` time* — rejected: that call only happens for items that were
  downloaded and analyzed, i.e. exactly the items that are *not* being refreshed.

---

## R5 — Reuse `capture_outcomes`, or add a table for missed refreshes?

**Decision**: Reuse `capture_outcomes`. Add one `capture_kind` value (`metric_refresh`) by widening
its CHECK to a strict superset. Use the existing `not_attempted` outcome with a descriptive reason
for aged-out items; no change to the failure-reason vocabulary is needed.

**Rationale**: `capture_outcomes` is already keyed `(platform, content_id)` — deliberately, so a
capture failing before a signal row exists is still recordable — is already append-only, and already
carries `run_id`/`account_id`. That is exactly the shape a missed metric refresh needs.

Critically, the existing `chk_capture_outcomes_reason` constrains the reason vocabulary **only when
`outcome = 'failed'`**. An aged-out item is `outcome = 'not_attempted'`, which the constraint does
not restrict, so recording it requires no vocabulary change and no loosening of the rule that a
genuine failure must carry a classified reason.

**Rationale for accepting the CHECK widening**: the vocabulary is closed at the database boundary on
purpose (feature 005), so extending it necessarily means replacing the constraint. The replacement
is a superset — every currently-valid value remains valid — so it cannot reject an existing row, and
the down-migration restores the original exactly. Recorded in the plan's Complexity Tracking.

**Alternatives considered**: A dedicated `missed_refreshes` table — rejected: identical shape to
`capture_outcomes`, and it would make "why is this value missing" require a UNION across two tables,
reintroducing the ambiguity feature 005 exists to remove.

---

## R6 — How is legacy seeding timestamped without inventing a capture time?

**Decision**: Seed each pre-existing row as one observation stamped with its existing
`harvested_at`, marked `provenance = 'legacy'`.

**Rationale**: `harvested_at` is a real recorded time — the moment the value was written — so using
it invents nothing. What it is *not* is a capture time near publication, which is why the provenance
marker matters: a velocity interval anchored on a legacy endpoint rests on a value observed at an
unknown point in the item's life, and FR-023a requires that stay visible.

Marking provenance rather than withholding the row keeps the observation record complete — no
captured value lives only in `harvested_signals` — which is what makes the derived history status
(Q3) trustworthy: "has no history" becomes a statement about observations, not about which table
someone remembered to check.

**Alternatives considered**:
- *Not seeding at all, marking the signal row instead* — rejected by the Q3 clarification: it
  reintroduces a maintained flag and leaves values living only in the old single-row table.
- *Seeding with the migration timestamp* — rejected: it asserts the value was observed at a time it
  was not, which Principle VI forbids outright.

---

## R7 — How is derivation made re-runnable without duplicating rows?

**Decision**: Key `velocity_intervals` on its ordered observation pair
`(from_observation_id, to_observation_id)` with a unique constraint, and upsert on re-derivation.

**Rationale**: Because observations are immutable, an interval is a pure function of its two
endpoints plus configuration. Re-running with unchanged thresholds reproduces identical values;
re-running after a threshold change updates the plateau/acceleration verdict in place, which is
exactly the intended behaviour (FR-016c) and what makes the provisional defaults revisable once a
corpus exists.

Overwriting a *derived* row does not conflict with Principle VII, which governs observations. The
plan records this explicitly so a future reader does not mistake it for a violation.

**Alternatives considered**: Append-only derived rows with a "current" flag — rejected as
unnecessary machinery for values that are reproducible from immutable inputs, and it would make the
simple "one row per consecutive pair" query a filtered one for no gain.

---

## R8 — Does a metric refresh threaten existing stored analysis?

**Decision**: Yes, and it must be structurally prevented. `social.observation.record` writes only to
`metric_observations`. The separate latest-value update (FR-010) must not write analysis or reviewer
fields at all.

**Evidence** — [social_tasks.py:290-294](../../service/prefect/tasks/social_tasks.py#L290-L294):

```sql
shares = COALESCE(EXCLUDED.shares, harvested_signals.shares),
subtitle = EXCLUDED.subtitle,
content_flow = EXCLUDED.content_flow,
summary = EXCLUDED.summary,
```

`shares` is `COALESCE`d — with a comment explaining that absence is not evidence of zero — while the
three analysis fields are overwritten unconditionally. A refresh carries no analysis output, so
routing one through this path would blank `subtitle`, `content_flow`, and `summary` on every
refreshed row.

**Rationale**: This is silent, irreversible destruction of output that this feature never
re-creates (analysis is out of scope, and re-running it would cost model spend that FR-025 forbids).
FR-010a exists because of this finding.

**Alternatives considered**: Passing the existing analysis values back through the upsert —
rejected as a read-modify-write that races with a concurrent analysis and restates data the row
already holds. The refresh path simply must not touch those columns.

---

## Summary of resolved unknowns

| ID | Unknown | Resolution |
|---|---|---|
| R1 | Size of the pre-existing set | 819 rows and growing; define by presence at migration time, not a literal |
| R2 | Free re-observation reach | 567 of 819 (69.2%) reachable; 252 permanently frozen, recorded as `aged_out_of_listing` |
| R3 | Which metrics yield velocity | Likes dense (791/793); views/comments 338; shares zero — derive per metric, never blended |
| R4 | Refresh insertion point | Single pass over `all_items`, after account resolution, before the download loop |
| R5 | Missed-capture storage | Reuse `capture_outcomes`; widen `capture_kind` to a superset; `not_attempted` needs no reason-vocabulary change |
| R6 | Legacy observation timestamp | Existing `harvested_at`, marked `provenance = 'legacy'` |
| R7 | Re-runnable derivation | Unique on the ordered observation pair; upsert |
| R8 | Refresh vs stored analysis | Real destruction risk; refresh must not touch analysis columns (FR-010a) |

**No NEEDS CLARIFICATION markers remain.**
