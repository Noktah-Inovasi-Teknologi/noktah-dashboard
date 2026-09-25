# Phase 0 — Research: Structured Extraction Output

**Feature**: `007-structured-extraction` | **Date**: 2026-08-03

Everything below was measured against the live system on 2026-08-03 unless explicitly marked as
inference. Where a claim could not be measured without spending money or a platform request, it is
labelled and left as an implementation-time probe rather than asserted.

---

## R1 — The corpus is 819 signals / 855 items, not 656 / 691

**Decision**: Size the backfill against "everything present when the migration runs", not a literal
count. Update the spec's figures; keep the audit's numbers only where they are cited as a July
snapshot.

**Measured**:

| | Count |
|---|---|
| `harvested_signals` | **819** |
| `harvested_items` | **855** |
| items with `analysis_status = 'success'` | 837 |
| items with `analysis_status = 'failed'` | 18 |

By content type, with analysis coverage:

| content_type | rows | with subtitle | with flow | with summary |
|---|---|---|---|---|
| video | 364 | 350 | 351 | 351 |
| carousel | 323 | 6 | 323 | 323 |
| image | 130 | 0 | 130 | 130 |
| story | 2 | 0 | 2 | 2 |

**Rationale**: The spec inherited 656/691 from `docs/AUDIT.md`, a 2026-07-31 snapshot. Feature 006
hit the same drift (its Phase 0 corrected 656 → 819). The corpus grows monthly, so any fixed count
in a requirement is wrong by the time it ships.

**Note on the 6 subtitled carousels**: `analyze_item` routes by *file extension*, not by the
`content_type` label — an all-video "carousel" goes to `analyze_video` and gets a real transcript
([analyze.py:344-346](../../service/roach/analyze.py#L344-L346)). This is the mixed-carousel edge
case in the spec, and it confirms provenance must record the path actually taken (FR-024).

**Alternatives considered**: Freezing the backfill scope at a snapshot count — rejected; it would
silently exclude everything harvested between planning and execution.

---

## R2 — Every item has retained media, but there is no way to fetch it back

**Decision**: Backfill sources media from Drive. A new task `google.drive.download-file` must be
built — it does not exist.

**Measured**:
- `855 / 855` items have a non-empty `drive_file_id`. **Zero** items lack a Drive reference.
- `service/prefect/tasks/google_tasks.py` defines `drive_file_upload` (:367) and `drive_file_delete`
  (:455). **There is no download.**
- OAuth scopes are `drive.readonly` + `drive.file`
  ([google_credentials.py:84-85](../../service/prefect/blocks/google_credentials.py#L84-L85)).
  `drive.file` covers files this app created — which is exactly the harvested media — and
  `drive.readonly` covers reading. **No re-consent needed.**

**Rationale**: FR-034 forbids re-contacting the platform, and local files are unlinked immediately
after upload ([social_harvest.py:928](../../service/prefect/flows/common/social_harvest.py#L928)).
Drive is therefore the only lawful source, and the gap is a missing read path, not a missing
permission.

**Caveat, not measured**: a `drive_file_id` being present in Postgres does not prove the Drive
object still exists. Deletion, trashing, or a permissions change would surface only at fetch time.
This is precisely why FR-035 requires a classified skip rather than a silent one — the failure mode
is real and cannot be pre-empted from the database.

**Alternatives considered**: Re-downloading from the platform — rejected outright by FR-034 and
Constitution X. Keeping local copies going forward — rejected as scope creep and a storage
commitment this feature does not need.

---

## R3 — There is no cost baseline anywhere. The first projection cannot be calibrated.

**Decision**: Ship a `--pilot N` mode that runs a small measured sample and records real usage. Until
a pilot has run at the current version, the dry-run reports **uncalibrated** and states so, rather
than presenting a modelled guess in the same field as a measurement.

**Measured**:
- `docker logs roach | grep -c "openrouter] usage"` → **0**.
- Prefect's `log` table, `message LIKE '%usage call_site%'` → **0 rows**.
- No cost, usage, or spend table exists in `noktah_dashboard` (16 tables; none of them).
- `_log_usage` in [analyze.py:140-161](../../service/roach/analyze.py#L140-L161) `print()`s to
  stdout and returns. `_call_model` returns only the parsed JSON — **the usage object is discarded
  before it leaves the function**, so even a caller that wanted it could not have it.
- `/analyze` returns `{ok, content_id, analysis: {subtitle, flow, summary, status, error}}`
  ([api.py:139-143](../../service/roach/api.py#L139-L143)) — no usage field.

**Rationale**: FR-033 says the projection must be derived from measured per-item usage *where a
measured baseline exists*, and must say so where it does not. Today it does not exist for any class
of item. Constitution VI forbids an estimate and an observation sharing a field, so the honest first
dry-run reports "no measured baseline" — and the pilot is the cheapest way to stop that being true.

Two consequential changes fall out: roach must **return** usage on `/analyze` (not just log it), and
the Prefect side must persist it per extraction (FR-038). That also makes the cost record a global
monthly ceiling could later be enforced against — see Complexity Tracking in `plan.md`.

**Alternatives considered**: Estimating from OpenRouter's published price list × a token guess —
rejected; it produces a number indistinguishable in form from a measurement, which is the exact
failure Constitution VI names. Deriving from the audit's figures — rejected; the audit found the
same absence ("Instrumentation: ABSENT").

---

## R4 — The token budget has headroom, but the evidence for that is right-censored

**Decision**: Keep the 8,000-token video budget for v1; make `finish_reason == "length"` a hard
validation failure (FR-017) and re-measure once truncation is actually detectable.

**Measured** (character lengths of stored values):

| content_type | max subtitle | avg subtitle | p95 subtitle | max flow | avg flow |
|---|---|---|---|---|---|
| video | 4,812 | 766 | 1,789 | 2,290 | 891 |
| carousel | 296 | 161 | 0 | 1,949 | 685 |
| image | — | — | 0 | 896 | 493 |
| story | — | — | 0 | 462 | 459 |

The largest stored transcript is ~4.8k characters — roughly 1.2k–1.6k tokens for Indonesian/English
code-mixed text. Against an 8,000-token ceiling, adding a beat array and an attribute block (order
500–1,500 tokens) fits with room to spare.

**The caveat that matters**: this distribution is **right-censored by the bug this feature fixes**.
`analyze.py` never checks `finish_reason`, and `_extract_json`'s regex fallback salvages a truncated
response ([analyze.py:171-174](../../service/roach/analyze.py#L171-L174)). A transcript that *was*
cut off would have been stored looking complete. So "max observed = 4,812" is a lower bound on the
true maximum, not the maximum. It is evidence of headroom, not proof of it.

**Rationale**: FR-011 forbids trading transcript completeness for structure. The right first move is
not to raise the ceiling speculatively — it is to make truncation *visible*, then size the budget
against honest data. Once FR-017 lands, a truncation is a counted quarantine event rather than a
silent one, and the budget can be raised on evidence.

**Alternatives considered**: Pre-emptively raising the video budget to 16,000 — rejected for v1; it
doubles worst-case output spend to solve a problem whose size is currently unmeasurable. Revisit
after the first month of quarantine counts.

---

## R5 — Both services can validate without a new dependency

**Decision**: Validate with **pydantic**, already present in both images.

**Measured**: `service/roach/pyproject.toml` declares no validation library, but pulls `fastapi`,
which depends on pydantic v2 — and `api.py` already uses it (`class AnalyzeRequest(BaseModel)`,
[api.py:72](../../service/roach/api.py#L72)). `service/prefect/pyproject.toml` pulls `prefect` and
`pydantic-settings`, both pydantic-backed.

**Rationale**: Constitution III pins dependencies in `uv.lock`; adding `jsonschema` to two images to
do what pydantic already does is unjustified. Pydantic also produces structured, quotable error
messages, which FR-018 requires feeding back to the model verbatim.

**Alternatives considered**: `jsonschema` — rejected as a redundant dependency. Hand-rolled checks —
rejected; FR-016's "no unvalidated path may exist" is much easier to hold with a declarative model
than with scattered `if` statements.

---

## R6 — Validation and the retry belong in roach, not Prefect

**Decision**: roach validates its own model output, performs the single error-fed-back retry, and
returns either a validated structure or a quarantine envelope. Prefect persists whichever arrives.

**Rationale**: FR-018 requires the retry to carry the validation error *back to the model*. Only
roach holds the model call, the prompt, and the media payload; routing the retry through Prefect
would mean shipping base64 media back and forth across the network to re-ask a question roach is
already positioned to ask. Roach stays stateless — it returns a value and stores nothing — which is
the existing division of responsibility and the spec's stated assumption.

**Error-contract detail that is easy to get wrong**: `.claude/rules/backend/roach.md` is explicit
that the `{ok: false, code, reason}` envelope is for *platform* failures, and that roach's own bugs
must propagate as 500s rather than be dressed as platform failures — the harvest flow *branches* on
that classification. A schema-validation failure is neither: it is a model failure. It must
therefore be a **third shape** — `200 OK` with `{ok: true, analysis: {status: "quarantined", ...}}`
— matching how analysis failure is already reported today (`status: "failed"` inside a 200 body,
[analyze.py:368](../../service/roach/analyze.py#L368)). Returning 4xx/5xx for a quarantine would
make the harvest flow treat a model quirk as a platform block and back off from a healthy profile.

**Alternatives considered**: Prefect validates what roach returns — rejected; it cannot retry
without re-sending media. Both validate — accepted in part: Prefect re-validates at the storage
boundary as a cheap guard (FR-016's "no unvalidated path"), but does not own the retry.

---

## R7 — The schema and vocabulary ship as version-controlled config, mounted read-only

**Decision**: Declare the extraction JSON Schema and the beat-function vocabulary **once**, in
version-controlled files under `config/`, mounted read-only into both containers. Sync the
vocabulary into Postgres with a flow, exactly as feature 005 does for field availability.

**Rationale**: The schema is used in three places — roach's `response_format`, roach's client-side
validation, and Prefect's storage-boundary re-validation. Declared separately in each, they drift,
and the drift is silent. There is direct precedent:
`./config/field_availability.yaml:/app/config/field_availability.yaml:ro`
([docker-compose.yml:49](../../docker-compose.yml#L49)) with
`flows/field_availability_sync.py` mirroring it into a table.

roach currently mounts only `data/`, `secrets/`, and the shared `social_data` volume, so this adds
one compose mount. Roach source is baked into its image and **still requires a rebuild for code
changes** — but with the schema mounted, a *schema version* can be added without one.

**Rationale for DB as well as file**: Constitution XII requires the vocabulary be queryable,
migratable state — a file alone cannot answer "was this beat's function valid under the version it
was assigned?" in SQL (SC-002). The file is the write path; the table is the read path. Same split
as `config/field_availability.yaml`.

**Alternatives considered**: Vocabulary in `hashmap.py` — explicitly forbidden by Constitution's
Data Management section. Schema in the database only — rejected; roach would need a DB connection,
breaking its statelessness. Duplicating the schema in both services — rejected as the drift risk
above.

---

## R8 — The calibration overlap is the 455 image-class items, and it may be lossy

**Decision**: Scope the controlled comparison to `image`, `carousel`, and `story` items. Treat a
per-item failure on either model as a **classified exclusion that shrinks the reported sample**,
never as a silent drop.

**Measured**: 455 of 819 signal rows are image-class (`carousel` 323 + `image` 130 + `story` 2).
364 are video. Deployed models are genuinely different:

| Path | Model | Source |
|---|---|---|
| video | `xiaomi/mimo-v2.5` | code default; `OPENROUTER_MODEL` **unset** in roach |
| image | `google/gemini-2.5-flash-lite` | `OPENROUTER_IMAGE_MODEL` **set** in `service/roach/.env` |

So FR-027d's "both paths on the same model" is *not* the current state — the comparison is
meaningful today — but it remains one unset env var away, because `IMAGE_MODEL` falls back to
`MODEL` ([analyze.py:26](../../service/roach/analyze.py#L26)).

**Inference, not measurement** — flagged deliberately: the reason `OPENROUTER_IMAGE_MODEL` exists at
all is recorded in the source as the video model's providers not reliably accepting image input,
alongside a history of "image-422 failures"
([analyze.py:22-31](../../service/roach/analyze.py#L22-L31)). If that is still true, sending the
455 image-class items to `xiaomi/mimo-v2.5` for the comparison will produce provider errors on some
fraction of them. **That is itself a finding** — the two models are not equally capable on the one
media class where they overlap — and it must be reported, not worked around by quietly dropping the
failures and reporting agreement over the survivors.

**Implementation-time probe required (one call, not a batch)**: send a single image item to
`xiaomi/mimo-v2.5` and record the outcome before committing to a calibration sample size. This has
not been done here because it costs a real model call, and Constitution XI's "deterministic first"
plus `.claude/rules/backend/roach.md`'s "probe sparingly" both argue against exploratory spend
during planning.

### ✅ PROBE RESULT (T042, measured 2026-08-08) — the inference above is now FALSE

One image sent to `xiaomi/mimo-v2.5`. **It was accepted**, first attempt, no retry:

```
model_served : xiaomi/mimo-v2.5
provider     : DeepInfra
attempts     : 1
beats        : 2          subtitle: 119 chars
cost_usd     : 0.00098352
```

**Consequences, and they change the plan:**

1. **The overlap is real and full-size.** The video model accepts image input today, so the
   controlled comparison can cover the whole image class rather than some attrition-reduced
   fraction of it. The "lossy overlap" caveat this section was built around does not apply, and
   the calibration sample does not need to be inflated to survive expected failures.
2. **The reason `OPENROUTER_IMAGE_MODEL` exists has expired.** It was introduced because the video
   model's providers refused images; that is no longer the observed behaviour. This does **not**
   mean the image model should be removed — it is ~4.5× cheaper per item ($0.00043 vs $0.00193
   measured), which is a perfectly good reason to keep it. But the *stated* reason in
   [analyze.py:22-31](../../service/roach/analyze.py#L22-L31) is now stale and should be re-worded
   to cite cost rather than capability.
3. **Provider fallback is live and observable.** The request was routed to **DeepInfra**, which is
   not in `PROVIDER_ORDER` (`xiaomi, digitalocean, novita, parasail`) — `allow_fallbacks: true`
   did what it says. The *model* still matched what was requested, so this pair would be usable in
   a calibration; but it is a concrete demonstration that "what we asked for" and "what answered"
   are genuinely different questions, which is exactly why FR-024 requires `model_served` be read
   off the response and why `calibration.agreement.compute` excludes mismatched pairs.

**Sample size decision**: no attrition allowance is needed. `CALIBRATION_MIN_SAMPLE` stays at 30
usable pairs, and the full 455-item image class is eligible.

⚠️ **Still one-directional.** The image model cannot accept video, so the comparison remains unable
to say anything about the 364 video items (FR-027a). This probe widens the overlap; it does not
close it.

**Alternatives considered**: Comparing on video by swapping the image model in — impossible; a
vision-only model cannot take video. Extrapolating image agreement to video — forbidden by FR-027a,
and it is the exact overreach the clarification chose the controlled comparison to avoid.

---

## R9 — New tables, not new columns on `harvested_signals`

**Decision**: Migration `009_structured_extraction` adds new tables. `harvested_signals` gains
**nothing**.

**Measured**: `harvested_signals` is `UNIQUE (platform, content_id)` — exactly one row per item. It
also carries `chk_signal_account CHECK (account_id IS NOT NULL)` and an FK to `accounts` (features
004/006 landed; migrations through `008_observation_history` are applied, so **009 is next**).

**Rationale**: One item must be able to hold several extractions — different schema versions
(FR-029), the second backfill when S-05 lands (FR-043b), and calibration pairs (FR-027). A
one-row-per-item table has no slot for that. This is the same reasoning that produced
`metric_observations` in feature 006, and the same key choice applies: key extractions by
`(platform, content_id)` rather than `harvested_signals.id`, so history survives the failure-retry
purge that deletes signal rows.

**Alternatives considered**: JSON column on `harvested_signals` — rejected; FR-007 requires beats be
filterable and countable without text parsing, and a JSONB blob makes SC-002's per-version
vocabulary check awkward and unindexed. Overwriting `content_flow` with a serialised structure —
rejected; it breaks FR-041 and the additive assumption.

---

## R10 — Backfill must never write to `harvested_signals`

**Decision**: The forward path writes both the new extraction rows and the existing prose columns.
**Backfill writes only the new extraction tables.**

**Rationale**: Re-extraction produces a *new* subtitle from the model. If backfill wrote it to
`harvested_signals.subtitle`, it would overwrite the stored transcript — violating FR-009 outright
and destroying data this feature cannot regenerate without paying for it again.

This is the same class of bug `.claude/rules/backend/songbird.md` documents for metric refresh,
where routing through `social.signal.record` instead of `social.signal.record-metrics` would blank
stored analysis "silently, and this feature cannot regenerate it". The lesson transfers exactly:
**a re-processing path must write a strict subset of what the original path wrote.**

**Alternatives considered**: Backfill updating prose columns "to keep them fresh" — rejected; it is
the failure mode above wearing a helpful face.

---

## R11 — Beat-function vocabulary v1 is deliberately minimal: four functions plus a residual

**Decision**: v1 members are `hook`, `setup`, `main_point`, `call_to_action`, and `unclassified`.
Nothing else.

**Rationale**: FR-004 requires the four concepts the current prompt already names. The temptation is
to add `demonstration`, `proof`, `offer`, `transition` up front — but FR-005 exists precisely to
tell us which ones are needed: the residual share is the measurement. Starting minimal means the
first month's `unclassified` rate is *evidence* for v2's membership rather than a guess ratified in
advance. A larger v1 also gives the model more latitude, which depresses the agreement rate R8 will
measure without anyone being able to say why.

Adding a member in v2 is additive and cheap under Constitution XII. Removing one is not.

**Alternatives considered**: A richer 8–10 term taxonomy — rejected as premature; it pre-empts the
finding FR-005 is designed to produce. Free-text function with post-hoc clustering — rejected;
FR-006 requires a closed vocabulary enforced at validation.

---

## R12 — "Batch over interactive" means our own loop; no provider batch API applies

**Decision**: Batching is the backfill flow's own paced loop over items. No provider batch endpoint
is used.

**Rationale**: Constitution XI requires batching "where the provider supports it". OpenRouter's
chat-completions surface, which both paths use, is per-request; the media payloads are base64 data
URLs inside the message body, so there is no batch shape to submit. The obligation is therefore
satisfied by making re-extraction a scheduled batch operation rather than an interactive one —
which it already is — plus the content-hash cache (FR-039) that keeps unchanged input from being
re-processed at all.

**Alternatives considered**: Waiting for a provider batch API — rejected; it does not exist for this
call shape, and the feature does not depend on it.

---

## Open items carried into implementation

| # | Item | Why not resolved here |
|---|---|---|
| 1 | Does `xiaomi/mimo-v2.5` accept image input reliably today? (R8) | Costs a real model call; one probe at implementation time, not exploratory spend during planning. |
| 2 | True maximum transcript length (R4) | Unmeasurable until truncation detection ships. Re-measure after one month of quarantine data. |
| 3 | Per-item cost by content type (R3) | No baseline exists. Established by the `--pilot` run, which is part of the build. |
| 4 | Whether every `drive_file_id` still resolves (R2) | Only observable at fetch time; handled as a classified skip by design. |
