# Noktah Hub (web)

The internal dashboard for managers, at **https://hub.noktah.co**. Nuxt 4 + Nuxt UI, TypeScript,
Bun, deployed as the Cloudflare Worker `noktah-hub` (auto-deploys on merge to master).

**This app only renders pages and forms.** Every data read/write and every AI call goes to
the Python API (`service/api`) in Docker on the office PC, reached through the Cloudflare
tunnel. Never put database access or OpenRouter calls here.

## Pages

| Route | What |
|---|---|
| `/` | Klien: the Registry list (active by default), completeness, team |
| `/clients/[id]` | One Client: tabs Ringkasan, Profil, Guideline, Permintaan, Registry, Riwayat |
| `/clients/[id]/intake` | Intake: paste text, a screenshot, a Google Doc or a PDF → Proposals to decide |
| `/approvals` | Persetujuan: Guideline changes waiting for the Brand Manager / Owner |
| `/people`, `/people/[id]` | Orang: people, emails, roles, leaving |
| `/notes` | Catatan lama: old AnythingLLM notes, the "belum ada klien" list |
| `/no-access` | Signed in to Cloudflare, but no Manager role in the Hub |

## Access

- Cloudflare Access lets any signed-in email through (G-14); **the Hub decides access** from
  its own roles (email → Person → role). A sign-in with no Manager role only ever sees
  `/no-access`.
- `workers_dev` and `preview_urls` are `false` in `wrangler.jsonc`: those default addresses
  would bypass the login gate. Keep them off.
- `server/utils/hubApi.ts` forwards to the API with the Worker's service token and the
  manager's own Access JWT (`X-Hub-User-Jwt`); the API verifies both. API errors pass through
  with their code (`no_access`, `conflict`, `ai_cap_reached`, …); a down PC or tunnel
  becomes a 503 in Bahasa.

## Commands

```bash
bun install
bun run dev        # local dev server
bun run lint       # eslint
bun run test:ui    # static UI conventions
bun run ui:gate    # rendered sweep: every page × width × light/dark × data state
bun run ui:sweep   # same, reporting instead of failing, with screenshots
bun run deploy     # build + deploy (normally done by Cloudflare on merge)
```

The sweep runs on sample data (`server/fixtures/`, enabled by `NUXT_HUB_FIXTURES=1` and the
`hub-fixture` cookie: `full`, `empty`, `down`, `no_access`, `failed`, `cap`), so it needs
no Docker and no sign-in. A new page gets a route in `e2e/ui-sweep.e2e.ts` and fixtures for
each of its states. The pre-push hook refuses a push touching `service/web/app` until the
gate is clean. See `.claude/skills/ui-sweep/`.

## Settings

Server-only settings go in `runtimeConfig` in `nuxt.config.ts` and are read with
`useRuntimeConfig(event)`. On Cloudflare, set them as Worker variables/secrets named
`NUXT_<KEY>` (e.g. `NUXT_API_BASE_URL`); locally, in `service/web/.env`.
