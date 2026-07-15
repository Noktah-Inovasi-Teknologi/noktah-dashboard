# Quickstart: Social Profile Content Harvest & Analysis

Validation guide proving the harvest → analyze → deliver path end-to-end. Implementation details
live in `tasks.md`; contracts in [contracts/](./contracts/) and entities in
[data-model.md](./data-model.md).

## Prerequisites

1. **Env vars** (in `.env` and `service/roach/.env`, all gitignored):
   - `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN` — refresh token **re-minted
     with the widened scopes** `drive.file` + `spreadsheets` (see research R2).
   - `OPENROUTER_API_KEY` — content analysis.
   - `ROACH_API_KEY` — shared secret for the roach API; set in `.env` (used by both `roach` and
     `prefect` via `docker-compose.yml`, which also wires the internal `ROACH_API_URL=http://roach:8080`
     and `HARVEST_DB_URL` automatically from `POSTGRES_USER`/`PASSWORD`/`DB` — neither needs to be
     set by hand).
   - `HARVEST_DRIVE_PARENT_ID` — id of the stable parent Drive folder that holds all per-profile
     folders + per-run Sheets (the dedupe anchor; see research R2/R6).
2. **Instagram burner cookies**: export `service/roach/secrets/cookies.txt` (Netscape format) from a
   logged-in throwaway account (research R3). TikTok needs no cookies.
3. **Postgres**: `harvested_items` table applied via `config/postgres/init.sql` (fresh DB) or the
   idempotent migration (existing DB).

## Setup

```bash
# Re-mint Google refresh token with Drive-write + Sheets scopes
cd service/prefect && uv run python run_google_oauth.py   # consent screen must list drive.file + spreadsheets

# Build/refresh the stack (roach now serves an API on 8080 internally, published as 8081 on
# the host — 8080 is reserved on the host for the local Google OAuth redirect flow)
docker-compose up -d --build roach prefect postgres
docker-compose ps                      # roach + prefect + postgres healthy
curl -s http://localhost:8081/health   # -> {"status":"ok"}
```

## Scenario 1 — Single TikTok profile, most-recent-N (P1 MVP, SC-001/SC-002)

```bash
docker exec prefect python flows/social_harvest_recent.py \
  --profiles "https://www.tiktok.com/@publicaccount" --n 5
```
**Expected**: a Drive folder named after the account holds up to 5 downloaded items; one run-level
Google Sheet has one row per item with metadata **and** `subtitle`/`content_flow`/`summary`
(sheet-schema.md). Flow returns a `summary` dict with `items_collected` and `sheet_id`.

## Scenario 2 — Instagram all four formats, time-window (SC-003)

```bash
# Relative window (last N days) — suited to the recurring schedule
docker exec prefect python flows/social_harvest_window.py \
  --profiles "https://www.instagram.com/publicaccount/" --days 7

# Absolute window (explicit date range, inclusive) — suited to a manual backfill
docker exec prefect python flows/social_harvest_window.py \
  --profiles "https://www.instagram.com/publicaccount/" \
  --start-date 2026-06-01 --end-date 2026-06-30
```
**Expected**: videos (Reels) fetched via the yt-dlp path and images/carousels/stories via the
gallery-dl path (FR-005) all appear in the profile's Drive folder, each as a Sheet row of the
correct `content_type`. Items with no spoken audio have an empty `subtitle`. Supplying
`--start-date`/`--end-date` selects by absolute date range (inclusive) and overrides `--days`.

## Scenario 3 — Resilience: one profile blocked (SC-005, US2)

Run ≥2 profiles where one returns `429`/challenge from roach.
**Expected**: run logs show back-off + the profile marked `blocked`/deferred; the other profiles
complete; already-collected items from the blocked profile remain in Drive and the ledger; the run
ends `Completed` (not failed).

## Scenario 4 — Rate cap & pacing (SC-004)

**Expected** (from Prefect run logs): requests are sequential with 5–10 s randomized gaps; no more
than 100 items collected in any rolling hour; a run with >5 profiles is rejected/truncated to 5
with a clear message.

## Scenario 5 — Idempotent re-run (FR-021)

Re-run Scenario 1 unchanged.
**Expected**: previously collected items are **skipped** (ledger hit) — not re-downloaded, no
duplicate Sheet rows; `summary.items_skipped_dedup` > 0.

## Scenario 6 — Observability & notification (SC-006/SC-007, US3)

**Expected**: Prefect run logs contain structured per-profile/per-item entries (counts, delays,
back-offs, failures); a completion notification is delivered via the configured Prefect notification
block. Triggering the same flow via its scheduled deployment produces identical logs + notification.

## Automated checks

```bash
cd service/prefect && uv run pytest tests/test_social_harvest.py tests/test_social_tasks.py
cd service/roach   && uv run pytest            # roach API contract tests
```
Cover: hourly-cap limiter, randomized-delay omission on last item, back-off + profile deferral,
dedupe skip, continue-on-item-failure, and the roach `/list` `/download` `/analyze` contracts.
