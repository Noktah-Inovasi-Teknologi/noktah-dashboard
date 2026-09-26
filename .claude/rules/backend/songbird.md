# Songbird Content-Generation Development Rules

## Overview

Songbird (feature `003-songbird-content-generation`) generates targeted marketing content — a **monthly
content plan** (scheduled) and **on-demand incidental pieces** (manual) — biased toward audience "hits"
(explicitly **not guaranteed**) by learning from what already performs well. It is **Prefect-only**: no new
service. Generation is prompt-assembly + one OpenRouter chat call, so it lives entirely in the existing
`service/prefect` image. See `specs/003-songbird-content-generation/` for spec, plan, data-model, and
contracts.

## Architecture

Songbird is the middle layer of an existing pipeline — it **consumes** signals and **hands off** to the
content-plan → Jira flow; it never calls Jira directly:

```
knowledge-base (client identity)  ┐
social-harvest + roach (what hits)├─► SONGBIRD ─► content-plan worksheet ─► content_plan_…_jira flow ─► Jira
marketing params + Clients config ┘
```

Layout mirrors social-harvest (thin flow entrypoints + a `flows/common/` engine + single-responsibility
tasks):

- **flows/common/songbird.py** — `run_generation(...)`: the shared engine. Gathers signals → builds the
  Indonesian/code-mixed prompt → calls OpenRouter (`array_schema`) → validates ideas → assigns dates
  (monthly only) → delivers draft or live. Never raises; returns the standard
  `{start_time, end_time, data, summary, error}` dict (constitution I/V).
- **flows/songbird_monthly_plan.py** — `songbird-monthly-plan` flow: dated, per-client quantity from the
  Clients worksheet, `target` `draft` (default) or `live`.
- **flows/songbird_batch_plan.py** — `songbird-batch-plan` flow: the monthly plan for one client or
  many. `clients` takes a single name or a comma/semicolon/newline-separated list; `month` defaults
  to next month. **Unscheduled.** Each client is generated independently and a per-client failure is
  recorded in `summary.failures` without stopping the batch — a partial set of plans beats none. The
  run is only marked failed when *every* client failed.
- **flows/songbird_generate.py** — `songbird-generate` flow: standalone, draft-only, no dates, no live.
- **tasks/songbird_tasks.py** — `songbird.client.context`, `songbird.signal.top-performers`,
  `songbird.config.content-mix`, `songbird.config.own-handles`.
- **tasks/songbird_ranking.py** — pure scoring/selection (no I/O), see "Ranking" below.
- **tasks/songbird_themes.py** — theme induction (`songbird.theme.induce`) + Thompson allocation.
- **tasks/songbird_trends.py** — pure burst detection over caption/hashtag frequency.
- **tasks/openrouter_tasks.py** — `openrouter.chat.complete` (+ `object_schema`/`array_schema`), the
  reusable text-only OpenRouter task (ported from roach's provider-routing/structured-JSON pattern).
  Every call takes `call_site` + `client` and logs the `usage` object OpenRouter returns
  (`prompt_tokens`/`completion_tokens`/`cost_usd`) to the **run** logger, so spend shows up per client
  and per bucket in the Prefect UI. `X-Title`/`HTTP-Referer` split this service from roach on
  OpenRouter's own dashboard. Passing a new `openrouter_chat` call without `call_site`/`client` makes
  that spend unattributable — always set both.

## Signals (the "hit" bias)

1. **Client facts** — the Hub's **Client Card** first: current Profil and Guideline values (every one accepted by a manager) plus open client requests, found by exact client key or alias (`_client_card` in `tasks/songbird_tasks.py`). The PIC field stays out of the prompt: it is the team's contact, not content. Only when a client's card is still empty does songbird fall back to the pre-Hub `knowledge_records` (feature 001), fuzzy-matched by client. `summary.client_facts` records which source a run used (`client_card`, `knowledge_records`, or null).
2. **Own + competitor performance** — top `harvested_signals` rows by engagement (likes+comments) over a
   rolling **180-day** window (configurable). Handles come from `hashmap.py::CLIENT_SOCIAL`
   (`client → {own[], competitors[], competitor_profiles[]}`) merged with per-run params.
   `CLIENT_SOCIAL` is read from the **CLIENT_SOCIAL block of the "Hashmaps" worksheet** (cols R/S/T,
   one row per competitor, grouped by client; competitor URLs are normalized to the bare handle that
   matches `harvested_signals.profile_key`). The sheet configures **competitors only** — `own` is
   always `[]` there, so pass the client's own handle via the `own_handles` run param. Not every
   client has competitor rows; those clients fall back to KB + marketing params (FR-011).

   **A CLIENT_SOCIAL row is configuration, not collection.** Naming a competitor handle does not
   harvest it — `harvested_signals` only holds what a `harvest-monthly-*` deployment actually
   collected. For a while all four configured competitor handles had zero rows, so every "own vs
   competitor" comparison silently ran on one side of the data. Each competitor now has a
   `harvest-monthly-comp-<handle>` deployment in `prefect.yaml` with the *same* `days: 31` monthly
   shape as the client blocks — the ranking is a within-account comparison, which only means
   something when both sides are sampled identically, so **if you change one side's window, change
   the other in the same commit**. **Adding a row to the sheet still requires adding a deployment
   block**; after editing, `docker exec prefect prefect deploy --all`.

   **Since feature 004-relational-spine, this is queryable without the sheet.**
   `roster-sync` mirrors `CLIENT_SOCIAL` (and the Clients worksheet's own-handle columns) into
   `client_account_roles`, so "which accounts are competitors of which client" is now a SQL join
   (`client_account_roles.role = 'competitor'`), not something that only exists inside a running
   songbird call. `hashmap.py::CLIENT_SOCIAL` remains the **write path** the sheet is reconciled
   from — songbird's own retrieval (`songbird_top_performers`, `_resolve_handles`) still reads the
   sheet-backed hashmap directly and has not been switched onto the new tables; that migration is
   a later change, not part of 004. See `.claude/rules/backend/schema.md` and
   `specs/004-relational-spine/quickstart.md` step 7 for the query.
3. **Marketing params** — platform, audience, goal, tone, content pillars, quantity.

`harvested_signals` is populated **best-effort** by the social-harvest engine (`social.signal.record`)
alongside each delivered item — a signal-write failure never aborts a harvest. Songbird is a pure consumer.

## Ranking (`tasks/songbird_ranking.py`)

Exemplars are **not** ranked by raw `likes + comments`. Measured on the live store, that flat sum
returned 8/8 video and 6/8 from one account for a 3-competitor set, with **zero Post exemplars**
while the plan asked for 4 Posts. Three data facts drive the design:

- Instagram exposes `comments` **and** `views` only on video here, so a flat sum ranks by format.
- Account baselines differ ~8× (median 25 vs 3), so absolute engagement ranks accounts, not content.
- `corr(views, likes) = 0.251` — reach is nearly orthogonal to engagement.

The score answers *"did this overperform for its account, in its format"*:

```
p_* = midrank / (n+1), shrunk toward 0.5 by n/(n+SHRINK_K)   # NOT percent_rank()
video: 0.5*p_eng + 0.5*p_views, then dampened by rate        # non-video: p_eng
final: (0.7*within_account + 0.3*within_bucket_global) * recency(half-life 90d, floor 0.65)
```

Non-obvious constraints — **do not "simplify" these without re-reading `test_songbird_ranking.py`**:

- **Never `percent_rank()`.** It returns 0 for a single-row partition, making that account
  permanently unselectable. The Weibull position gives n=1 → 0.5 (neutral, selectable).
- **Midranks for ties are mandatory** — `_coerce_count` quantizes `"1.2K"` → 1200, so ties cluster
  and selection would otherwise be non-deterministic across runs.
- **Engagement rate may only *dampen*, never boost.** `rate = eng/views` is anti-correlated with
  views by construction (measured **−0.60**); as a positive term it cancels the reach signal and a
  972-view/5-engagement post outranks a 545,178-view Reel.
- **`GLOBAL_BLEND` is safe to raise** because *diversity is enforced by the per-account cap in
  `select_exemplars`, not by the score*.
- Ads (`advertisement = true`) are excluded — bought reach is not evidence the creative works.
- Exemplar quota is apportioned to the content mix, so a plan needing Posts sees Post exemplars.

## Strategy layers

**Theme allocation** (`songbird_themes.allocate_slots`) decides variety vs doubling down as a batched
bandit: each theme's exemplar scores form a posterior, slots are Thompson-sampled. Thin evidence ⇒
wide posteriors ⇒ slots spread (variety); strong evidence ⇒ narrow ⇒ repeats (close content). Guard
rails: no theme exceeds `MAX_THEME_SHARE` (40%), ≥15% of slots reserved for untested themes, and the
cap relaxes to an even split when there are too few themes to make 40% feasible. Exploit slots
instruct a **new angle**, never a restatement — repeated creative is what drives fatigue.

**Trend detection** (`songbird_trends.detect_trends`) is Kleinberg-style burst scoring: token rate in
the last 21 days vs the preceding 69, weighted by how well the carrying posts performed. Requires
≥3 recent occurrences; `#term` and `term` are deduped.

Both are **best-effort** — a failure logs and generation proceeds without that block (FR-011).

## Rules

- **Never call Jira.** Handoff is the content-plan worksheet only (FR-018).
- **The draft matches the "DRAFT v5" content-plan layout exactly** — same 20 columns, same order:
  `No.`, `Tanggal`, `Waktu`, `Bentuk`, `Topik`, `Creator`, `Format`, `Purpose/Theme`,
  `Strategic Application`, `Kebutuhan Personil`, `Known Facts`, `Shoot Guide`, `Visualisasi Konten`,
  `Asset`, `Caption`, `Keterangan`, `Approval`, `Link Referensi`, `TicketID`, `Key`.
  Songbird fills only `GENERATED_COLUMNS`; scheduling/production/workflow columns (`Waktu`,
  `Kebutuhan Personil`, `Asset`, `Approval`, `TicketID`, `Key`, …) are deliberately left blank.
  `Creator` is always `Brand`, and `Format` **mirrors** `Bentuk` (it is derived, not generated).
  The draft carries **no rationale columns** — `adapted_pattern` / `source_exemplar` / `rationale`
  and the hit disclaimer live in the run-outcome JSON instead, satisfying FR-005/FR-006 without
  deviating from the plan format. `convert_content_plan_row_to_jira_issue` reads
  `Topik`/`Tanggal`/`Bentuk` (+ `Format`, `Purpose/Theme`, `Strategic Application`, `Shoot Guide`),
  all present. See `contracts/content-plan-row.md`.
- **Dates are engine-assigned, never model-assigned** (FR-004). The prompt must not ask for dates.
- **Language**: primarily Bahasa Indonesia with natural English code-mixing (brand names, hashtags,
  loanwords, taglines) — no force-translation; mirror the client's own style (FR-003a).
- **Hits are patterns to adapt, not to copy**, and are **not guaranteed** — the `HIT_DISCLAIMER` rides in
  every `summary` and the draft's `Hit Note` column (FR-006).
- **Degrade gracefully** — `summary.grounding` records which of the four states a plan is in:
  | state | means |
  |---|---|
  | `knowledge_base+signal` | both available (normal) |
  | `signal_only` | **no KB ⇒ generate purely from own + competitor harvested content.** The prompt says the KB is unavailable and makes harvested content the sole brand reference — infer voice/positioning/terminology from the client's own posts first, competitors second — and forbids inventing facts (`[PLACEHOLDER: …]`). The exemplar budget is widened by `NO_KB_EXEMPLAR_BOOST` since it is the only grounding left. |
  | `knowledge_base_only` | nothing harvested yet |
  | `params_only` | neither; warn loudly, still generate (FR-011) |
  Continue on a single malformed idea, counting `ideas_failed` (FR-020).
- **Client identity is decided by tokens, not trigram similarity.** Nearly every client is
  "Klinik Mata …"/"Klinik Utama …", so the shared prefix dominates `similarity()` — measured,
  `klinik mata smec bitung` tied at **0.467** against *both* `klinik mata bireuen` and
  `klinik mata sampang`, and a Bitung plan was grounded in Bireuen's KB. `_is_same_client` requires
  one name to be a **token-subset** of the other; similarity now only gathers candidates. A client
  with no KB gets zero records, never another brand's. Do not loosen this back to a threshold.
- **Quantity** for the monthly plan comes from the Clients worksheet (fail-fast if unconfigured, FR-003b),
  overridable per run. Do NOT hardcode a default count.
- **One OpenRouter call per content type, topped up until the quota is met.** A client contracted for
  9 Short Videos gets 9. Asking for a mixed batch and correcting it afterwards was the old design and
  it lost ideas — the model returned the wrong split, the overflow was discarded, and the deficit
  bucket stayed short. Now `bentuk` is never requested (the call *is* the type), and
  `_generate_bucket` re-requests any shortfall up to `MAX_TOPUP_ATTEMPTS`, passing the existing topics
  so top-ups don't repeat. Duplicate topics are rejected within a bucket.
- **`shoot_guide`/`visualisasi_konten` briefs are per content type** (`_FORMAT_BRIEFS`), written to
  reproduce how the live plans fill them: `Shoot Guide` is the capture plan (shot/angle/movement per
  scene, `-` for Posts); `Visualisasi Konten` is the content itself (per-slide Visual/Headline/Body
  for Posts with slide 2 a standalone hook; per-scene VISUAL/TOS/DIALOG for videos). The live
  sheets have **both** columns and no `Reference` column — a draft column the live sheet lacks is
  silently blanked by the name-aligned append, which is how `Reference` used to vanish on `--target live`.
- **Draft** = content-plan columns + reviewer rationale columns; **live** = name-aligned append, rationale
  columns dropped (FR-016/FR-016a).

## Running

```bash
# Monthly plan → draft (default)
docker exec prefect python flows/songbird_monthly_plan.py --client "Ecky Dental Center" --month "Agustus 2026"
# Monthly plan → live content-plan worksheet
docker exec prefect python flows/songbird_monthly_plan.py --client "Ecky Dental Center" --month "Agustus 2026" --target live
# On-demand incidental (draft-only, no dates)
docker exec prefect python flows/songbird_generate.py --client "Ecky Dental Center" --quantity 3 --platform instagram

# Deploy (songbird-monthly-plan has a monthly cron; songbird-generate is manual)
docker exec prefect prefect deploy --all
docker exec prefect prefect deployment run 'songbird-generate/songbird-generate' --param client="Ecky Dental Center" --param quantity=3
```

## Config

Env (Prefect + prefect-worker): `OPENROUTER_API_KEY` (required — now needed by Prefect, previously
roach-only), `SONGBIRD_DRIVE_PARENT_ID` (draft location). The model is not env: it comes from
`shared/noktah_ai/models.yaml` (case `generation`), rotating after 3 consecutive failures
(`shared/noktah_ai/rotation.py`); pass `model=` to `openrouter_chat` only to force one. Optional Clients
sheet overrides: `SONGBIRD_CLIENTS_SPREADSHEET_ID`/`_TAB`/`_NAME_COLUMN`,
`SONGBIRD_CONTENT_TYPE_COLUMNS`.
Defaults match the live sheet: name column `Name`, content-type columns `Post,Story,Short Video`.

**`--target live` writes to the client's own plan, and there is no env override.** The target is
the Google Sheet named exactly `Content Plan - {client} - {Indonesian month}` in the client's
`Content Plan Folder ID`, on `Sheet1` or else the first tab. This is the same rule the Jira flow
reads with (`tasks/content_plan_files.py`). It is resolved **before** generation, so a missing
or ambiguous plan costs no model spend. It is never created: the planner makes the file. Nothing
is appended to a tab lacking `Tanggal`/`Bentuk`/`Topik`. The old default (`SONGBIRD_LIVE_*`) was
the Clients workbook's `Clients` tab, so live runs appended blank rows to the client roster. An
explicit `live_spreadsheet_id` is still accepted for one client, and refused in a multi-client
batch.

## How much to generate (per content type)

Monthly amounts are **per content type**, read from the Clients worksheet — one column per type:

| Name | … | Post | Story | Short Video |
|------|---|------|-------|-------------|
| Ecky Dental Center | | 4 | 4 | 4 |

`songbird.config.content-mix` returns `{"Post": 4, "Story": 4, "Short Video": 4}`; the plan's **total is
their sum** (12) and its **composition is enforced**, not left to the model. Rules:

- Column headers double as the `Bentuk` vocabulary — they match what live content plans already use, so
  rows drop into the content-plan → Jira flow with no translation.
- A **zero/blank amount means that type is never generated** (e.g. Gudang Karung Jumbo has no Story).
- The prompt states the exact breakdown, but `_apply_content_mix` in the engine is the authority: it
  buckets ideas by canonical `bentuk`, caps each type at its quota, and round-robin interleaves them so
  publish dates alternate types. Common model synonyms (`Reels`/`Carousel`/`IG Story`) are normalized;
  unrecognizable ones are dropped and counted in `ideas_failed`.
- Overrides: `--quantity N` for a flat total (model picks the type, mix disabled), or
  `--content-mix "Post=4,Story=2"` for explicit per-type amounts.
- Fail fast (FR-003b) when the client row is missing or every amount is zero — never guess a count.

---

**Last Updated:** 2026-07-15
**Feature:** 003-songbird-content-generation
