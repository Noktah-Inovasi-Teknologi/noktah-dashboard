# Noktah Hub API

The Python (FastAPI) service behind **Noktah Hub** (`hub.noktah.co`, code in `service/web`).
It is the only writer of the Registry, the Client Cards and People, and it runs the AI
Intake and Summaries. The web app never touches the database or OpenRouter.
Spec: `specs/008-hub-registry-client-card/`.

## How a request reaches it

```
manager ─login─▶ hub.noktah.co (Cloudflare Worker, Access app "Noktah Hub")
                   │  + service token noktah-hub-worker
                   │  + the manager's Access JWT as X-Hub-User-Jwt
                   ▼
               hub-api.noktah.co (Access app "Noktah Hub API", policy "Hub Worker only")
                   ▼  Cloudflare tunnel
               container hub-api:8000 ─▶ Postgres (noktah_dashboard)

Prefect (Docker network only) ─X-Hub-Internal-Token─▶ hub-api:8000/internal/*
```

- **No published port.** The container is reachable only through the tunnel (`/v1/*`) and,
  from the Docker network, by Prefect (`/internal/*`).
- **Two proofs per `/v1` request** (`app/auth.py`): the Access JWT for this API's
  application (the call came through Access with the Hub's service token), and the
  manager's own Hub login JWT (*who* is acting). A plain email header is never trusted.
- **Roles are the Hub's, not Cloudflare's** (G-11). Email → Person → active roles; only
  Manager roles sign in. What each role may do is `app/permissions.py` (tested as a table).
- **`/internal/*`** needs `X-Hub-Internal-Token` AND no Cloudflare headers; anything else
  gets 404, so the routes aren't advertised.

## Layout

| Path | What |
|---|---|
| `app/registry/` | Clients, team, accounts (`service.py`), the change log, the one-time import (`importer.py`), the sheet copy (`sheet_copy.py`, pure planning + one write) |
| `app/card/` | Card definition (`config/hub/card_v1.yaml`, frozen once used), append-only values, approvals |
| `app/intake/` | Sources (text, screenshot, Google Doc, PDF), the `intake_v1` prompt, deterministic checks, the pipeline |
| `app/summary/` | The Ringkasan, from confirmed values only, at most once a day |
| `app/ai/` | OpenRouter call (length checked before parsing, one quoted retry, no salvage), the monthly cap |
| `app/people/` | People, emails, roles (grant rules in `permissions.may_grant`) |

## Rules that are easy to undo by accident

- **Nothing is deleted** (G-32). Card values are superseded, corrected or rejected;
  Clients go inactive; people leave; account links deactivate.
- **Nothing from Intake reaches a card until a Manager decides** (FR-035). Ticks are
  enforced by the API, not only the UI: `from_image` needs `image_checked`, `not_pic`
  needs `pic_confirmed`, and `unverified` must be edited.
- **The AI cap is enforced before the call** (`ai/budget.check_budget`, USD 5/month by
  default). At the cap, Intake answers 402 and Summaries pause; direct editing still works.
- **The sheet copy writes only the Hub's cells** (the nine Clients columns and the five
  Hashmaps blocks' key/value columns), only when they differ, and never before the import
  (`hub_sync_state.last_success_at` is NULL until then). Internal Clients (Eskala) stay out
  of the Clients tab but keep their Hashmaps rows. Hashmaps keys keep their live spelling.
- **Validate-only is the default** for everything that writes outside the Hub or spends:
  the import (`validate_only=true`, a rolled-back transaction) and the sheet copy (`dry_run`).

## Settings (env)

| Variable | Meaning |
|---|---|
| `HUB_API_DATABASE_URL` | Postgres DSN |
| `HUB_API_ACCESS_API_AUD` | AUD tag of the "Noktah Hub API" Access application (required, not secret) |
| `HUB_API_INTERNAL_TOKEN` | Shared with the Prefect containers; empty disables `/internal/*` |
| `OPENROUTER_API_KEY` | Intake and Summary calls |
| `HUB_INTAKE_MODEL` / `HUB_SUMMARY_MODEL` | Default `xiaomi/mimo-v2.5` (images verified 2026-09-25, research R4) |
| `HUB_AI_MONTHLY_CAP_USD` | Default 5 |
| `GOOGLE_CLIENT_ID` / `_SECRET` / `_REFRESH_TOKEN` | Google Docs intake, the import, the sheet copy |
| `SLACK_AUTOMATION_NOKTAH` | 80%-of-cap alert |
| `SLACK_MANAGERIAL_ESKALA` / `SLACK_MANAGERIAL_NOKTAH` | "Guideline change waiting" notices, by Noktah Brand |
| `PREFECT_API_URL` | Only to pause `roster-sync` after the real import |

## Commands

```bash
uv sync && uv run pytest              # all tests; real-Postgres ones need HUB_TEST_DATABASE_URL
docker-compose up -d --build api      # rebuild + restart after code changes (source is baked in)
docker logs -f hub-api
```

Real-Postgres tests (`@pytest.mark.schema`) build a fresh `hub_test` database from the
migration chain per test and are skipped when no server is reachable. Default DSN:
`postgresql://noktah:noktah_local_dev@localhost:5432/hub_test`.
