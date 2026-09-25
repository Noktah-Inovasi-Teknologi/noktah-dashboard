# Noktah Hub (web)

The internal dashboard for managers, at **https://hub.noktah.co**. Nuxt 4 + Nuxt UI, TypeScript,
Bun, deployed as the Cloudflare Worker `noktah-hub` (auto-deploys on merge to master).

**This app only renders pages and forms.** Every data read/write and every AI call goes to
the Python API (`service/api`) in Docker on the office PC, reached through the Cloudflare
tunnel. Never put database access or OpenRouter calls here.

## Pages

| Route | What |
|---|---|
| `/` | Klien: the Registry list, filters (status, brand, team member, card), pages of 20, Tambah klien |
| `/clients/[id]` | One Client: tabs Ringkasan, Profil, Guideline, Permintaan, Registry, Riwayat |
| `/clients/[id]/intake` | Intake: paste text, a screenshot, a Google Doc or a PDF → Proposals to decide |
| `/approvals` | Persetujuan: Guideline changes waiting for the Brand Manager / Owner |
| `/people`, `/people/[id]` | Orang: filters (status, Unit, role), pages of 20; one form per Person (name, IDs, emails, Units, roles, permissions), leaving |
| `/no-access` | Signed in to Cloudflare, but no Masuk Hub permission in the Hub |

## Forms and lists

- **Save is enabled only when something changed.** Every edit form compares itself with
  what is stored (`composables/useFormState.ts`: strings trimmed, blank = empty, emails and
  roles as sets), so an untouched form, a saved one, or an edit typed back to the original
  all leave Save disabled. A new-record form (Tambah klien, Tambah orang) enables Save once
  its required fields are filled.
- **Roles come from the API's catalog** (`/v1/me` → `catalog`; `useCatalog()` for names).
  Never list roles in the web app: a new role is a row in `unit_roles`, not a code change.
  The person form picks Units, then roles from those Units, then permissions (a new role
  ticks its usual ones). A Client's team slots and who may fill them come from the same
  catalog: each picker lists only people holding that role in the Client's brand.
- **Tables and long lists filter and page in the browser** (`composables/usePaged.ts`,
  `components/ListPager.vue`): 20 rows a page, 10 for Permintaan. Changing a filter returns
  to page 1.

## Access

- Cloudflare Access lets any signed-in email through (G-14); **the Hub decides access** from
  its own permissions (email → Person → permissions). A sign-in without Masuk Hub only
  ever sees `/no-access`.
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
