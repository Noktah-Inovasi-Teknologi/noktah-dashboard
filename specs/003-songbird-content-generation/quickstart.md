# Quickstart: Songbird — Targeted Content Generation

End-to-end validation of the feature. Run inside the running Docker stack. Details of shapes live in
[data-model.md](./data-model.md) and [contracts/](./contracts/).

## Prerequisites

- Stack up: `docker-compose up -d` (postgres, prefect, prefect-worker, roach).
- Env (root `.env`, gitignored): `OPENROUTER_API_KEY` set; `GOOGLE_*` OAuth vars set;
  `HARVEST_DRIVE_PARENT_ID` and `SONGBIRD_DRIVE_PARENT_ID` set; `ROACH_API_KEY` matching roach.
- `config/postgres/init.sql` applied (fresh volume, or run the `harvested_signals` DDL against the
  existing db).
- `hashmap.py::CLIENT_SOCIAL` has an entry for the test client (own + ≥1 competitor handle).
- The Clients worksheet (`1-aV46TIn4m_zs3vtCNeS_Bvl3Tt-tgg09uuG_NqgNNY`) has a quantity value for the test
  client (confirm the quantity column header and set `SONGBIRD_QUANTITY_COLUMN` if it differs from the
  default).

## Scenario 1 — Populate performance signal (US4, FR-012..014)

Harvest one own + one competitor handle so songbird has signal:

```bash
docker exec prefect python flows/social_harvest_recent.py \
  --profiles "https://www.instagram.com/<own>" "https://www.instagram.com/<competitor>" --n 10
```

Verify the signal store filled and ranks:

```bash
docker exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "SELECT profile_key, likes, comments, published_at FROM harvested_signals \
   ORDER BY (COALESCE(likes,0)+COALESCE(comments,0)) DESC LIMIT 5;"
```

**Expected**: rows for both handles with engagement + analysis; ranked by engagement.
**Resilience check (FR-014)**: the harvest run's summary shows delivered items even if a signal write logged
a warning.

## Scenario 2 — Monthly plan, draft (US1, P1)

```bash
docker exec prefect python flows/songbird_monthly_plan.py \
  --client "<Client>" --month "Agustus 2026" --target draft
```

**Expected**:
- A draft Google Sheet appears under `SONGBIRD_DRIVE_PARENT_ID` named for the client + month.
- Row count == the client's configured monthly quantity (read from the Clients sheet).
- Every row has `Topik`, an in-month `Tanggal` (spread across Agustus 2026, not all one day), and `Bentuk`.
- Draft has the rationale columns (`Adapted Pattern`, `Source Exemplar`, `Rationale`, `Hit Note`).
- Content is Indonesian with natural English terms retained.
- `data/songbird_monthly_plan_*.json` summary has `ideas_produced`, `signal_available: true`,
  `target: "draft"`, and the `hit_disclaimer`.

## Scenario 3 — Monthly plan, live handoff (US3, P2, SC-003)

Against a **throwaway** content-plan tab:

```bash
docker exec prefect python flows/songbird_monthly_plan.py \
  --client "<Client>" --month "Agustus 2026" --target live \
  --live-spreadsheet-id "<throwaway_id>" --live-tab "<tab>"
```

Then confirm downstream consumption without reformatting:

```bash
docker exec prefect python flows/content_plan_spreadsheet_to_jira_issue.py \
  --month "Agustus 2026" --validate-only
```

**Expected**: generated rows land under the existing columns by name (no new/duplicate columns; no
rationale columns written); the content-plan flow validates them into Jira payloads with **no issues
created**.

## Scenario 4 — On-demand incidental (US2, P1)

```bash
docker exec prefect python flows/songbird_generate.py \
  --client "<Client>" --quantity 3 --platform instagram \
  --goal "promo flash sale" --tone "playful"
```

Or via deployment:

```bash
docker exec prefect prefect deployment run 'songbird-generate/songbird-generate' \
  --param client="<Client>" --param quantity=3 --param platform=instagram
```

**Expected**: exactly 3 standalone ideas in a draft; **no `Tanggal` assigned**; no live-worksheet write;
standard run-outcome dict.

## Scenario 5 — Graceful degradation (Edge cases, FR-011, SC-005)

Run Scenario 2 for a client with **no** harvested signal (empty/absent `CLIENT_SOCIAL` or unharvested
handles).

**Expected**: run still succeeds and produces the requested number of ideas; summary shows
`signal_available: false` and notes reduced grounding; no error.

## Scenario 6 — Per-idea failure isolation (FR-020, SC-007)

(Unit-test level — `tests/test_songbird.py`.) With a mocked model returning one malformed idea among N:
the run delivers N-1 ideas, `ideas_failed == 1`, and does not raise.

## Deployments

```bash
docker exec prefect prefect deploy --all
docker exec prefect prefect deployment ls   # shows songbird-monthly-plan (cron) + songbird-generate (manual)
```

**Expected**: `songbird-monthly-plan` has a monthly cron schedule; `songbird-generate` has none. Worker
logs show pickup on `noktah-pool`.
