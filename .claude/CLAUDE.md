# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Prefect workflow orchestration service for automating integrations with Google APIs (Sheets, Drive) and Jira. Built with Python and Prefect 3.x, running in Docker containers.

## Development Commands

### Prefect Workflows
```bash
# Run flows inside Prefect container (recommended)
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py

# Run for a specific month/year
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py --month "Juni 2026"

# Dry run (validate only, no Jira issues created)
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py --validate-only

# Access Prefect UI
# http://localhost:4200

# View flow logs
docker-compose logs -f prefect
```

### Prefect Deployments
```bash
# Deployments are defined in service/prefect/prefect.yaml and run on the
# noktah-pool process work pool, executed by the prefect-worker container.
# The social-harvest-* deployments have NO schedule — trigger them manually.

# (Re)register all deployments after editing prefect.yaml or flow code.
# prefect.yaml is bind-mounted into the container (docker-compose.yml), so edits
# apply without an image rebuild — but a container recreate is needed the first
# time that mount is added (`docker-compose up -d prefect`).
docker exec prefect prefect deploy --all
# Or a subset by name pattern:
docker exec prefect prefect --no-prompt deploy -n "harvest-monthly-*"

# List deployments / trigger an ad-hoc run of a deployment
docker exec prefect prefect deployment ls
docker exec prefect prefect deployment run 'social-harvest-recent/social-harvest-recent' \
  --param profiles='["https://www.tiktok.com/@name"]' --param n=5

# Stories-only harvest (ephemeral ~24h; run frequently, unscheduled for now)
docker exec prefect python flows/social_harvest_stories.py --profiles "https://www.instagram.com/lasikasyik/"

# Per-client monthly harvest: harvest-monthly-<client> deployments (one per client in
# the Clients sheet), monthly cron on the 1st, staggered 30 min apart from 17:30 WIB
# (slots 14-18 spill onto the 2nd, 00:00-02:00). Window = days: 31 — a run collects the
# month it just covered. Dedup means the corpus still accumulates month over month, so
# the window only bounds a single run's reach; a NEW account back-fills one month, not a
# quarter, so run `social-harvest-window --days 90` once by hand when onboarding one.
# These deployments pass NO harvest_name — the flow falls back to Prefect's
# auto-generated flow-run name, so a delivered sheet row traces to the run that made it.
docker exec prefect prefect deployment run 'social-harvest-window/harvest-monthly-ecky-dental-center'

# Per-competitor monthly harvest: harvest-monthly-comp-<handle>, one per handle in the
# CLIENT_SOCIAL block of the Hashmaps sheet. Same shape as the client blocks (days: 31,
# monthly) so both sides are measured identically — songbird's ranking is a
# within-account comparison, which only means anything on symmetric sampling.
# Runs 02:30-05:00 WIB on the 2nd, clear of the 03:00 social-harvest-sync cron.
# Adding a competitor row to the sheet does NOT create a deployment — add a block
# to prefect.yaml too, then redeploy.
docker exec prefect prefect deployment run 'social-harvest-window/harvest-monthly-comp-kmneyecare'

# Sheet→DB sync: reconcile harvested_signals + detail sheets from the canonical
# Account Social Harvest sheets (reviewer-edited `advertisement` flag). Scheduled daily.
docker exec prefect python flows/social_harvest_sync.py

# Worker logs (deployed run execution)
docker-compose logs -f prefect-worker
```
Note: the `social-harvest-*` deployments ship with empty `profiles` — pass real
profile URLs at run time (via the Prefect UI "Run" form or the `--param` above).
`social-harvest-stories` collects only currently-active Stories (Instagram + TikTok)
via roach's `stories_only` listing — it's unscheduled; run it often to catch stories
before they expire.

### Relational Spine (clients, accounts, roles, runs)
```bash
# Roster sync: reconcile clients/accounts/roles from the Clients + Hashmaps
# worksheets into the datastore. Daily cron (03:30 WIB), also triggerable on
# demand. Nothing gates a harvest on this having run (FR-022a) — an
# unregistered handle is simply skipped and reported until the next sync.
docker exec prefect python flows/roster_sync.py
docker exec prefect python flows/roster_sync.py --validate-only   # preview only, writes nothing

# Spine backfill: one-time link of existing harvested_signals /
# harvested_items / knowledge_records rows to accounts/clients. Run once,
# after roster-sync has populated the roster. Safe to re-run.
docker exec prefect python flows/spine_backfill.py
docker exec prefect python flows/spine_backfill.py --validate-only

# Ownership answerable in SQL, no spreadsheet — see
# specs/004-relational-spine/quickstart.md for the full query and expected shape:
docker exec postgres psql -U noktah -d noktah_dashboard -c "
  SELECT c.display_name, r.role, ah.handle_text, s.platform, count(*) AS posts
  FROM harvested_signals s
  JOIN accounts a ON a.id = s.account_id
  JOIN account_handles ah ON ah.account_id = a.id AND ah.is_current
  LEFT JOIN client_account_roles r ON r.account_id = a.id AND r.is_active
  LEFT JOIN clients c ON c.id = r.client_id
  GROUP BY 1,2,3,4 ORDER BY 1,2;"
```
See `.claude/rules/backend/schema.md` for the migration workflow and
`docs/roster-maintenance.md` for the two maintainer actions this introduces
(recording a handle rename, correcting a client name mapping).

### Field Availability & Capture Provenance
```bash
# "Can this field ever be known?" — determinations live in the version-controlled
# config/field_availability.yaml (NOT data/, which is gitignored) and sync into
# the field_availability table. Unscheduled: run after editing the YAML.
docker exec prefect python flows/field_availability_sync.py --validate-only
docker exec prefect python flows/field_availability_sync.py

# One-off: bring existing harvest sheet tabs to the current column layout after
# `shares` was appended. Idempotent; touches row 1 only, never reviewer data.
docker exec prefect python flows/sheet_header_backfill.py --validate-only
docker exec prefect python flows/sheet_header_backfill.py

# Why is this value empty? Every empty engagement value must resolve to exactly
# one cause. An `UNRESOLVED` row is a bug.
docker exec postgres psql -U noktah -d noktah_dashboard -c "
  SELECT CASE
      WHEN s.comments IS NOT NULL                   THEN 'present'
      WHEN fa.status = 'unavailable_platform_limit' THEN 'platform does not publish it'
      WHEN fa.status = 'not_collected_by_decision'  THEN 'deliberately not collected'
      WHEN fa.status = 'undetermined'               THEN 'availability undetermined'
      WHEN co.outcome = 'failed'                    THEN 'capture failed: ' || co.reason
      WHEN co.outcome = 'no_match'                  THEN 'capture ran, item not in result'
      WHEN co.id IS NULL                            THEN 'pre-feature row, provenance unknown'
      ELSE 'UNRESOLVED - this row is a bug' END AS why_empty, count(*)
  FROM harvested_signals s
  LEFT JOIN field_availability fa ON fa.platform = s.platform
       AND fa.content_type = s.content_type AND fa.field_name = 'comments'
  LEFT JOIN LATERAL (SELECT * FROM capture_outcomes c
       WHERE c.platform = s.platform AND c.content_id = s.content_id
       ORDER BY c.observed_at DESC LIMIT 1) co ON true
  GROUP BY 1 ORDER BY 2 DESC;"
```
A `NULL` metric is never self-explanatory: **`field_availability`** says whether
the field is knowable at all, **`capture_outcomes`** (append-only) says whether it
was obtained this run. TikTok share counts are now stored
(`harvested_signals.shares`) — roach always parsed them, they were discarded at
the storage boundary. Instagram publishes no share count anywhere, and Instagram
stories expose no public counts at all; both are recorded as platform limits
rather than left ambiguous. See `.claude/rules/backend/schema.md` and
`specs/005-signal-field-coverage/`.

### Longitudinal Metrics (observation history + velocity)
```bash
# Engagement is no longer frozen at first sighting. Every harvest now records one
# observation per LISTED item — including items its day-window filter discards —
# read off a listing that was happening anyway. Marginal platform requests: ZERO.
# No download, no analysis, no model call for an already-harvested item.

# One-time: carry pre-feature signal rows forward as `legacy` observations.
docker exec prefect python flows/observation_backfill.py --validate-only
docker exec prefect python flows/observation_backfill.py

# Derive velocity from stored observations (monthly cron, 06:00 WIB on the 2nd —
# after both harvest waves close). Reads the datastore only; a derivation failure
# cannot affect a harvest, and a blocked profile cannot prevent derivation.
docker exec prefect python flows/velocity_derive.py --validate-only
docker exec prefect python flows/velocity_derive.py
docker exec prefect python flows/velocity_derive.py --platform instagram

# Why does this item have no velocity? Exactly one reason applies.
# An `UNRESOLVED` row is a bug.
docker exec postgres psql -U noktah -d noktah_dashboard -c "
  SELECT reason, count(*) FROM velocity_status GROUP BY 1 ORDER BY 2 DESC;"

# One item's series, with its derived rates
docker exec postgres psql -U noktah -d noktah_dashboard -c "
  SELECT o.observed_at, o.provenance, o.likes,
         round(v.elapsed_seconds/86400.0, 2) AS days_since_prev,
         v.delta_likes, v.rate_likes_per_day, v.is_plateau, v.is_acceleration
  FROM metric_observations o
  LEFT JOIN velocity_intervals v ON v.to_observation_id = o.id
  WHERE o.content_id = '<id>' ORDER BY o.observed_at;"
```
**Velocity needs a second observation, which arrives with the next monthly harvest.**
On the day this ships a correct system reports nearly every item as `legacy_only`
with no velocity. That is the expected state, not a failure.

**Re-observability is bounded by listing depth, not by a day window** — the `days:`
filter is applied client-side *after* the listing returns, so an item stays
observable long after it leaves the harvest window. The monthly deployments list
at `DEFAULT_LIST_DEPTH = 150` (`social_harvest_window.py`; no deployment overrides
it), and no account holds more than 69 harvested items — so **the whole corpus is
currently reachable**. Measured live 2026-08-03: one `lasikasyik` listing returned
**182** items and produced 182 observations while collecting nothing.

When an item *does* eventually fall out of reach it is recorded as
`aged_out_of_listing` — a reported fact about how far the listing sees, **not** a
collection regression. Raising depth to chase one is collection expansion and out
of scope.

A metric refresh writes **only** metric columns. It must never touch `subtitle`,
`content_flow`, `summary`, or `advertisement` — `social.signal.record` overwrites
those unconditionally, which is why the refresh path uses
`social.signal.record-metrics` instead. Routing a refresh through the wrong one
would blank stored analysis on every refreshed row, silently, and this feature
cannot regenerate it (re-running analysis would cost model spend it forbids).

See `.claude/rules/backend/schema.md` and `specs/006-longitudinal-metric-capture/`.

### Structured Extraction (validated, versioned, queryable)
```bash
# Content flow is an ordered array of BEATS over a closed vocabulary, not prose.
# The forward path writes it automatically on every harvest — nothing to run.

# Vocabulary: config/extraction/vocabulary_v1.yaml -> extraction_vocabulary_terms.
# Unscheduled: run after editing the YAML. A version that any extraction has
# recorded is FROZEN and the sync fails loudly rather than altering it.
docker exec prefect python flows/extraction_vocabulary_sync.py --validate-only
docker exec prefect python flows/extraction_vocabulary_sync.py

# Backfill (COSTS MONEY). Defaults to --dry-run, which makes ZERO model calls.
docker exec prefect python flows/roach_extract_backfill.py --dry-run
docker exec prefect python flows/roach_extract_backfill.py --pilot 12   # establishes the baseline
docker exec prefect python flows/roach_extract_backfill.py --confirm

# Calibration (COSTS MONEY, ~2x per item — each is extracted by BOTH models).
docker exec prefect python flows/roach_extract_calibrate.py --dry-run
docker exec prefect python flows/roach_extract_calibrate.py --confirm
docker exec prefect python flows/roach_extract_calibrate.py --report    # zero calls

# The question the feature exists to answer — no text matching anywhere:
docker exec postgres psql -U noktah -d noktah_dashboard -c "
  SELECT b.function, count(*) AS items,
         round(avg(s.likes + coalesce(s.comments,0))) AS avg_engagement
  FROM content_extractions e
  JOIN extraction_beats b ON b.extraction_id = e.id AND b.position = 1
  JOIN harvested_signals s ON s.platform = e.platform AND s.content_id = e.content_id
  WHERE e.purpose = 'production' AND s.advertisement = false
  GROUP BY 1 ORDER BY 2 DESC;"

# Why does this item have no extraction? Exactly one reason. UNRESOLVED is a bug.
docker exec postgres psql -U noktah -d noktah_dashboard -c "
  SELECT reason, count(*) FROM extraction_status GROUP BY 1 ORDER BY 2 DESC;"
```
**Nothing unvalidated can reach storage (FR-016).** roach validates client-side, retries
**exactly once with the validation error fed back to the model**, then quarantines. Provider-side
`strict` is not relied on — routing crosses four providers with `allow_fallbacks`.

Three rules that are counter-intuitive and easy to undo:

- **A quarantine is `200 OK`, never 4xx/5xx.** The `{ok: false, code}` envelope means *platform*
  failure and the harvest flow **branches** on it — 429 backs off and rotates egress, 404 skips the
  profile. A model writing bad JSON would make a healthy profile look blocked.
- **Backfill writes a strict SUBSET of what the forward path writes** — only the new extraction
  tables, **never `harvested_signals`**. Re-extraction produces a new subtitle, and
  `harvested_signals` holds one row per item, so writing it back destroys the stored transcript
  irreversibly. Two guards enforce this: a static import guard and a before/after snapshot test.
- **Truncation is checked BEFORE parsing.** A beat array cut off after three complete beats is
  valid JSON satisfying the schema; only `finish_reason == "length"` can catch it. The old
  `\{.*\}` salvage regex and `_to_text`'s list coercion are **deleted**, not disabled, and a test
  greps the module to prove it.

Model routing is **roach's** to report, not something to infer from Prefect's environment: which
model a case uses depends on roach's in-memory rotation. Ask `GET /extraction-config`. Guessing
locally reports "no cross-model comparison applies" while roach is demonstrably serving two
different models.

### AI models: one accepted list per case (`config/ai/models.yaml`)
```bash
# Which models each case may use, best value first; edit, then restart (no rebuild):
docker-compose restart api roach prefect prefect-worker
# What roach is using right now, per case, with its rotation state:
curl -s -H "X-API-KEY: $key" http://localhost:8081/extraction-config
```
Six cases: `summary`, `intake` (pasted text, Docs, PDFs) and `intake_image` (screenshots,
scanned PDFs) in hub-api; `image` and `video` in roach /analyze; `generation` in songbird
(Prefect). Each starts on its first model; 3 consecutive failures move it to the next, and after
60 minutes on a fallback it tries the first again (`rotate_after`,
`back_to_first_after_minutes`). In Prefect every flow run is its own process, so rotation lasts
one run. The lists were chosen from benchmarks on real data, songbird's by blind judging (scores
and costs in the file's comments). No model thinks: reasoning made Intake worse and songbird
slower and costlier for no better score. Two rules:
**video models must take audio** (the subtitle is a verbatim transcript; a video-only model
"transcribes" by reading burned-in captions), and **every model must be served by a provider
the OpenRouter account allows** (openrouter.ai/settings/privacy), or its calls fail with 404.
`OPENROUTER_MODEL`, `OPENROUTER_IMAGE_MODEL`, `HUB_INTAKE_MODEL` and `HUB_SUMMARY_MODEL` no
longer choose anything.

Spend: `EXTRACTION_SPEND_THRESHOLD_USD` (per run, default $5) and
`EXTRACTION_MONTHLY_CEILING_USD` (hard stop, default $25). **Both cover extraction spend only** —
songbird's generation spend is not counted against them, so neither is a system-wide budget.
See `.claude/rules/backend/schema.md` and `specs/007-structured-extraction/`.

### Songbird Content Generation
```bash
# Monthly content plan → reviewable draft (default)
docker exec prefect python flows/songbird_monthly_plan.py --client "Ecky Dental Center" --month "Agustus 2026"

# Monthly plan appended straight into the live content-plan worksheet
docker exec prefect python flows/songbird_monthly_plan.py --client "Ecky Dental Center" --month "Agustus 2026" --target live

# Batch: one client or many, comma-separated (month defaults to next month)
docker exec prefect python flows/songbird_batch_plan.py --clients "Klinik Utama Gresik"
docker exec prefect python flows/songbird_batch_plan.py \
  --clients "Klinik Utama Gresik, Klinik Mata Sampang, Klinik Utama Sumenep"

# On-demand incidental content (standalone, draft-only, no dates)
docker exec prefect python flows/songbird_generate.py --client "Ecky Dental Center" --quantity 3 --platform instagram

# Deployments: songbird-monthly-plan (monthly cron); songbird-batch-plan and
# songbird-generate are unscheduled — trigger manually.
docker exec prefect prefect deploy --all
docker exec prefect prefect deployment run 'songbird-generate/songbird-generate' \
  --param client="Ecky Dental Center" --param quantity=3
docker exec prefect prefect deployment run 'songbird-batch-plan/songbird-batch-plan' \
  --param clients="Klinik Utama Gresik, Klinik Mata Sampang"

# Override the configured amounts for one run
docker exec prefect python flows/songbird_monthly_plan.py --client "MCafe" --month "Agustus 2026" \
  --content-mix "Post=4,Story=2,Short Video=3"
```
Monthly amounts are read **per content type** from the Clients worksheet's `Post` / `Story` /
`Short Video` columns — the total is their sum and the composition is enforced on the generated
plan (a zero means that type is never generated). Competitor handles come from the
`CLIENT_SOCIAL` block of the "Hashmaps" worksheet (see below); the client's **own** handles are
derived from the Clients worksheet's `Instagram`/`TikTok` URLs. Both merge with per-run
`own_handles` / `competitor_handles` params.

The draft sheet matches the **"DRAFT v5" content-plan layout** exactly (20 columns, incl. a
ready-to-post `Caption`); songbird fills only the content columns and leaves scheduling/production/
approval fields blank. Each content type is generated in its own call and **topped up until the
configured amount is met**, so a client contracted for 9 Short Videos gets 9.

Exemplars are scored **relative to each account's own baseline, per content type**, blending reach
(`views`) with engagement — a flat `likes + comments` ranking returned 8/8 video and 0 Posts. On top
of that, a bandit allocates the month between proven themes (repeat with a new angle) and untested
ones (explore), and burst detection surfaces rising topics. See
`.claude/rules/backend/songbird.md` for the constraints — several are counter-intuitive.

### Hashmaps (WORKERS / COMPONENTS / CONTENT_EDITOR / FIELD_ASSOCIATE / CLIENT_SOCIAL)
These mappings are **not hardcoded** — they live in the **"Hashmaps"** tab of the Clients workbook
(`1-aV46TIn4m_zs3vtCNeS_Bvl3Tt-tgg09uuG_NqgNNY`) and `hashmap.py` reads them at runtime. Add or
remove *rows* freely; only a change to the sheet's *columns* needs a code change
(`SHEET_LAYOUT` / `CLIENT_SOCIAL_LAYOUT` in `hashmap.py`).

```bash
# Entry counts per block (forces a fresh read of the sheet)
docker exec prefect python hashmap.py

# Dump one block as JSON
docker exec prefect python hashmap.py --block CLIENT_SOCIAL
```
Reads are cached in-process for `HASHMAP_TTL_SECONDS` (default 300) and snapshotted to
`data/hashmap_cache.json`, which is used as a fallback if the Sheets API is unreachable. Clients
absent from a block simply get no mapping (e.g. no Jira component, or no competitor signal for
songbird) — so a client must exist in the sheet to be wired up.

### Noktah Hub (managers' dashboard)
```bash
# Web: service/web (Nuxt 4 + Nuxt UI, TypeScript, Bun) → Cloudflare Worker `noktah-hub`
# at hub.noktah.co, behind Cloudflare Access. Auto-deploys on merge to master.
# API: service/api (FastAPI) → container `hub-api`, reached only through the tunnel
# at hub-api.noktah.co (service token). The ONLY writer of company data; the web
# app never touches the database or OpenRouter. See service/web/README.md and
# service/api/README.md.
docker-compose up -d --build api              # after API code changes (source is baked in)
cd service/api && uv run pytest               # API tests (real-Postgres ones need HUB_TEST_DATABASE_URL)

# Prefect → hub-api over the Docker network (/internal/*, X-Hub-Internal-Token).
# hub-api does the work; these flows only schedule it and alert #noktah-otomasi.
#   hub-summary-refresh  hourly :15   stale Ringkasan, max once/day/Client, AI cap applies
#   hub-sheet-sync       every 5 min  Registry → Clients/Hashmaps tabs (only changed Hub cells)
#   hub-sheet-check      Mon 06:00    same, full diff; rewrites cells edited in the sheet
#   hub-intake-purge     01:30 daily  Intake raw material > 12 months (excerpts kept forever)
#   hub-registry-import  manual       ONE-TIME sheet → Registry import; validate-only default
docker exec prefect python flows/hub_registry_import.py            # difference report, writes nothing
docker exec prefect python flows/hub_registry_import.py --apply    # real import; pauses roster-sync
docker exec prefect python flows/hub_sheet_sync.py --validate-only # cells it would write
```
**After the import the Hub is the only place Clients, teams and accounts are edited.** The
Clients and Hashmaps tabs become read-only copies (a note on A1 says so) that the
automations keep reading until they switch to the Registry (`docs/DEFERRED.md` A-1).
`roster-sync` is paused by the real import — don't unpause it: it would rewrite the roster
from the sheet, and migration 011 makes it fail on any new Client (no Noktah Brand).
The sheet copy refuses to write until the import has run, so its schedule is safe to deploy
early. Access is the Hub's own (email → Person → Units, roles, permissions; G-11, migration 012).
Roles live in one catalog (`unit_roles`: Noktah, Eskala, Venyu); a role grants nothing by
itself except Owner, and a Client team slot takes only someone holding its role. Card,
Intake and AI rules: `service/api/README.md`.
```bash
# UI gate: two halves. Static rules run after every edit under service/web/app
# (post-edit hook); the rendered sweep (every page × width × light/dark × data
# state, on sample data, no Docker needed) must be clean before a push/PR that
# touches service/web/app (pre-push hook). Full survey with screenshots: /ui-sweep
cd service/web && bun run test:ui && bun run ui:gate
```

### Database Backup (nightly)
```bash
# db-backup deployment: 02:00 WIB daily. pg_dump of noktah_dashboard →
# Drive: Company (restricted shared drive) > Backups > Database, newest 14 kept.
# Failures, and a backup under half the previous size (rotation is then skipped),
# post to #noktah-otomasi.
docker exec prefect python flows/db_backup.py --validate-only   # dump + verify, no upload
docker exec prefect python flows/db_backup.py                   # full run

# Restore into a SCRATCH database first, never over the live one. The
# "transaction_timeout" error is expected (pg_dump 17 vs server 15) and harmless.
docker exec postgres createdb -U noktah restore_check
docker cp noktah_dashboard_<date>.dump postgres:/tmp/b.dump
docker exec postgres pg_restore -U noktah -d restore_check --no-owner /tmp/b.dump
```

### Docker Environment
```bash
# Start all services
docker-compose up -d

# Start with rebuild
docker-compose up -d --build

# Graceful shutdown
docker-compose down

# View service status
docker-compose ps

# View logs (all services)
docker-compose logs -f

# View logs (specific service)
docker-compose logs -f prefect

# Monitor resource usage
docker stats
```

### Local Development (Python)
```bash
# Navigate to Prefect service
cd service/prefect

# Install dependencies with UV
uv sync

# Run flows locally
uv run python flows/content_plan_spreadsheet_to_jira_issue.py

# Run Google OAuth setup
uv run python run_google_oauth.py
```

## Architecture

### Services
- **Prefect**: Workflow orchestration server (Port 4200)
- **PostgreSQL**: Database for Prefect state persistence (Port 5432)

### Key Directories
- `service/prefect/` - Prefect workflow service
  - `flows/` - Workflow definitions
  - `tasks/` - Reusable task definitions
  - `blocks/` - Credential storage blocks
  - `data/` - Output data directory
- `config/postgres/` - Database initialization scripts
- `script/` - Setup and utility scripts
- `secrets/` - Secret files (not in version control)

### Prefect Service Structure
```
service/prefect/
├── Dockerfile          # Container definition with UV
├── pyproject.toml      # UV package manifest
├── uv.lock             # Locked dependencies
├── main.py             # CLI entry point
├── hashmap.py          # Sheet-backed data mappings (see "Hashmaps" below)
├── run_google_oauth.py # Google OAuth setup
├── flows/              # Workflow definitions
├── tasks/              # Task definitions
├── blocks/             # Credential blocks
└── data/               # Output directory
```

## Service Endpoints & Ports

| Service | Port | URL |
|---------|------|-----|
| Prefect UI | 4200 | http://localhost:4200 |
| PostgreSQL | 5432 | localhost:5432 |
| roach API | 8081 (host) → 8080 (container) | http://localhost:8081 |

### Health Check Endpoints
- **Prefect API**: http://localhost:4200/api/health
- **roach API**: http://localhost:8081/health (host); `http://roach:8080/health` on the internal Docker network

> ⚠️ **roach + Instagram/TikTok: always impersonate a browser.** Every request to these platforms
> (yt-dlp, gallery-dl, and any direct API call such as the IG Reels/clips endpoint) MUST use a real
> browser TLS fingerprint (`curl_cffi` / gallery-dl `browser=`) — a bare `requests`/`httpx` call is
> `429`/`403`-flagged even with valid cookies. roach's `/list` also merges the IG Reels tab and enriches
> Reel **views**/comments from the Reels-grid API. Editing roach source requires an image rebuild
> (`docker-compose up -d --build roach`); it is NOT volume-mounted. See `.claude/rules/backend/roach.md`.

## Configuration

### Environment Variables
Create `.env` file with the following variables:

```bash
# Database Configuration
POSTGRES_DB=your_database_name
POSTGRES_DB_DEV=your_dev_database
POSTGRES_DB_PREFECT=prefect
POSTGRES_USER=your_database_user
POSTGRES_PASSWORD=your_secure_password

# Google Services (for workflows)
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
GOOGLE_REFRESH_TOKEN=your_google_refresh_token

# Jira Integration
JIRA_URL=https://your-domain.atlassian.net
JIRA_USERNAME=your_email@domain.com
JIRA_API_TOKEN=your_jira_api_token

# Social Content Harvest (roach + social-harvest flows)
ROACH_API_KEY=your_roach_api_key           # must match service/roach/.env's ROACH_API_KEY
HARVEST_DRIVE_PARENT_ID=your_google_drive_parent_folder_id
# ROACH_API_URL and HARVEST_DB_URL are set automatically in docker-compose.yml
# (http://roach:8080 and a DSN built from POSTGRES_USER/PASSWORD/DB above) — no need to set here

# Failure alerts to Slack (every deployed flow, via flows/common/alerts.py)
SLACK_AUTOMATION_NOKTAH=https://hooks.slack.com/services/...  # #noktah-otomasi
SLACK_AUTOMATION_ESKALA=https://hooks.slack.com/services/...  # #eskala-otomasi
SLACK_AUTOMATION_VENYU=https://hooks.slack.com/services/...   # #venyu-otomasi

# Songbird content generation (songbird-* flows)
OPENROUTER_API_KEY=your_openrouter_api_key  # now needed by the Prefect services (was roach-only)
# (Generation models are listed in config/ai/models.yaml, case `generation`.)
SONGBIRD_DRIVE_PARENT_ID=your_drive_folder_id_for_draft_content_plans
# Optional Clients-sheet overrides (defaults target the content-plan workbook):
# SONGBIRD_CLIENTS_SPREADSHEET_ID, SONGBIRD_CLIENTS_TAB, SONGBIRD_CLIENTS_NAME_COLUMN,
# SONGBIRD_CONTENT_TYPE_COLUMNS
# (--target live has no env override: it writes to the client's own monthly plan sheet)
```

### Required External Services
- **Google APIs** - Sheets and Drive access for workflows
- **Jira** - Issue creation and management

## Technology Stack

### Core
- **Prefect** (3.6.9) - Workflow orchestration framework
- **Python** (3.14) - Runtime
- **UV** - Package manager

### Dependencies
- **asyncpg** - Async PostgreSQL driver for Prefect 3.x
- **pandas** - Data processing and transformation
- **atlassian-python-api** - Jira API integration
- **google-api-python-client** - Google Workspace APIs
- **google-auth** - Google authentication
- **pydantic-settings** - Configuration management

### Infrastructure
- **Docker** - Containerization
- **PostgreSQL 15** - Database

## Deployment Environment

### Infrastructure Setup
- **Platform**: Windows with Docker Desktop
- **Container Runtime**: Docker Desktop
- **Database**: PostgreSQL container with persistent volumes

### Current Services
- PostgreSQL (healthy)
- Prefect Server (healthy)

## Development Notes

### Current Implementation Status
- Prefect workflows with Google Sheets integration
- Prefect tasks for Google API and Jira operations
- Content plan spreadsheet to Jira issue flow (fully working)
- Docker environment with single compose file
- Health check endpoints configured

### Available Workflows
- `content_plan_spreadsheet_to_jira_issue.py` - Reads content plans from Google Sheets and creates Jira issues

#### Jira formatting (why rows used to fail)

**Measured, not assumed.** Across the 21 production runs saved under `service/prefect/data/`,
675 issues were submitted and exactly 10 were rejected — and **all 10 were the same error**:

```
{"summary": "The summary is invalid because it contains newline characters."}
```

A `Topik` cell wrapped over two lines in the sheet is invisible to whoever wrote it and fatal
to the issue. That is the entire observed loss.

**The description is enforced far more loosely than the ADF spec implies.** In the same corpus,
1,461 of 1,477 issues carried a newline *inside* a description text node and all 1,477 carried
an empty text node — and Jira accepted every one. So do not "fix" ADF description findings by
promoting them to rejections: they apply to nearly every row, and merging them with the summary
class buries the only class that costs content.

`tasks/jira_adf.py` holds the cleaning — pure functions, no Prefect, no I/O — split along
exactly that line:

- **`find_fatal_problems`** — what Jira actually rejects: a newline, control character,
  over-length (>255) or empty summary, and a description sent as a plain string.
- **`find_issue_problems`** — everything, fatal plus rendering-only, for diagnosis.
- **`sanitize_issue` / `sanitize_document`** — idempotent repair, applied by the converter, by
  `jira.issue-bulk.validate-issue-data`, and once more in `jira.issue-bulk.create`, so payloads
  written to disk by an earlier run are fixed on send too.

```bash
# Which rows would Jira reject, and why (creates nothing):
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py --validate-only
# Console prints only rejection-class repairs; step7_validation_per_client.json carries both
# (`rejections_prevented` vs `formatting_repairs`).
```

Two things to preserve when touching it:

- **Build description nodes with the builders** (`labelled_paragraph`, `labelled_block`,
  `document`), not hand-written `{"type": "text"}` dicts — they emit `hardBreak` for newlines
  and drop empty nodes, which is a *rendering* improvement, not a rejection fix.
- **The bold label's trailing space is load-bearing.** `"Approval: "` renders as
  `Approval:Selesai` without it, which is why `scrub` only trims space *before a newline* and
  leaves the ends of the string to `clean_text`.

Jira's per-issue rejection reasons are logged with the offending summary in
`jira.issue-bulk.create` — that logging is what made the diagnosis above possible.

Tests: `service/prefect/tests/test_jira_adf.py` (no database, no network).

### Running Workflows
```bash
# Execute a flow in the container (defaults to next month)
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py

# Run for a specific month and year (combined format)
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py --month "Juni 2026"
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py --month "June 2026"

# Run for a specific month and year (separate args)
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py --month-name Juni --year 2026

# Dry run — validate Jira issues without creating them
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py --validate-only

# Combine month targeting with validate-only
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py --month "Juni 2026" --validate-only

# View execution in Prefect UI
# http://localhost:4200
```

#### Flow Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `--month` | `str` | Target month in `"Month YYYY"` format, e.g. `"Juni 2026"` or `"June 2026"`. Omit to auto-use next month. |
| `--month-name` | `str` | Month name only (e.g. `"Juni"`). Must be paired with `--year`. |
| `--year` | `int` | Year (e.g. `2026`). Must be paired with `--month-name`. |
| `--validate-only` | flag | Dry-run mode — validates Jira issue data without creating issues in Jira. |

### Security Considerations
- Environment variables for all sensitive data
- Postgres authentication with SCRAM-SHA-256
- No secrets in version control

### Branches and PRs
`master` is the only long-lived branch. Every change reaches it through a short-lived
branch and a PR that the user merges. Branch names follow the venyu repo's format,
`<type>/<scope>/<Title-Case-Words>`:

- **type**: `feat` (new behaviour or a tweak), `fix` (a bug), `chore` (tooling, deps, scripts), `docs`.
- **scope**: the area the change lives in. `web` (service/web), `api` (service/api),
  `prefect`, `roach`, `config`. Use `project` when it spans more than one
  (e.g. a Hub change touching both web and api).
- **title**: a few capitalised words joined by hyphens, saying what the branch is for.

Examples: `feat/project/Noktah-Hub-tweaks`, `fix/web/Client-list-overflow-on-phones`,
`chore/prefect/Content-plan-helper-scripts`.

Don't open a PR until the user says so. Stage explicit paths: never sweep unrelated
uncommitted files into a branch.

Before a PR that adds or changes words people read (Hub UI text, captions and briefs,
songbird/Intake prompts), have the `copy-editor` agent (`.claude/agents/copy-editor.md`)
review them. It knows the glossary (`CONTEXT.md`), the Hub's Bahasa conventions and the
content-voice rules, and it reviews by default; tell it to apply when you want edits.
