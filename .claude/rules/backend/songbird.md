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
- **flows/songbird_generate.py** — `songbird-generate` flow: standalone, draft-only, no dates, no live.
- **tasks/songbird_tasks.py** — `songbird.client.context`, `songbird.signal.top-performers`,
  `songbird.config.quantity`.
- **tasks/openrouter_tasks.py** — `openrouter.chat.complete` (+ `object_schema`/`array_schema`), the
  reusable text-only OpenRouter task (ported from roach's provider-routing/structured-JSON pattern).

## Signals (the "hit" bias)

1. **Client knowledge** — current `knowledge_records` (feature 001), fuzzy-matched by client.
2. **Own + competitor performance** — top `harvested_signals` rows by engagement (likes+comments) over a
   rolling **180-day** window (configurable). Handles come from `hashmap.py::CLIENT_SOCIAL`
   (`client → {own[], competitors[]}`) merged with per-run params.
3. **Marketing params** — platform, audience, goal, tone, content pillars, quantity.

`harvested_signals` is populated **best-effort** by the social-harvest engine (`social.signal.record`)
alongside each delivered item — a signal-write failure never aborts a harvest. Songbird is a pure consumer.

## Rules

- **Never call Jira.** Handoff is the content-plan worksheet only (FR-018). Emit columns that
  `convert_content_plan_row_to_jira_issue` (`tasks/utility_tasks.py`) reads: `Topik`, `Tanggal`, `Bentuk`
  (+ `Format`, `Purpose/Theme`, `Strategic Application`, `Visualisasi Konten`). See
  `contracts/content-plan-row.md`.
- **Dates are engine-assigned, never model-assigned** (FR-004). The prompt must not ask for dates.
- **Language**: primarily Bahasa Indonesia with natural English code-mixing (brand names, hashtags,
  loanwords, taglines) — no force-translation; mirror the client's own style (FR-003a).
- **Hits are patterns to adapt, not to copy**, and are **not guaranteed** — the `HIT_DISCLAIMER` rides in
  every `summary` and the draft's `Hit Note` column (FR-006).
- **Degrade gracefully**: no signal ⇒ generate from KB + params, set `signal_available: false` (FR-011).
  Continue on a single malformed idea, counting `ideas_failed` (FR-020).
- **Quantity** for the monthly plan comes from the Clients worksheet (fail-fast if unconfigured, FR-003b),
  overridable per run. Do NOT hardcode a default count.
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
roach-only), `OPENROUTER_MODEL` (optional), `SONGBIRD_DRIVE_PARENT_ID` (draft location). Optional Clients
sheet overrides: `SONGBIRD_CLIENTS_SPREADSHEET_ID`/`_TAB`/`_NAME_COLUMN`, `SONGBIRD_QUANTITY_COLUMN`; live
target overrides: `SONGBIRD_LIVE_SPREADSHEET_ID`/`_TAB`. Confirm the Clients sheet's client-name and
quantity column headers against the live sheet and set the overrides if they differ from the defaults
(`Client` / `Jumlah Konten`).

---

**Last Updated:** 2026-07-15
**Feature:** 003-songbird-content-generation
