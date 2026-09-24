# Noktah Hub (web)

The internal dashboard for managers, at **https://hub.noktah.co**. Nuxt 4 + Nuxt UI, TypeScript,
Bun, deployed as the Cloudflare Worker `noktah-hub`.

**This app only renders pages and forms.** Every data read/write and every AI call goes to
the Python API in Docker on the office PC, reached through the Cloudflare tunnel. Never
put database access or OpenRouter calls here (see `.claude/CLAUDE.md`).

## Access

- `hub.noktah.co` is protected by Cloudflare Access (Zero Trust application "Noktah Hub",
  policy "Only managerials"). Only listed emails get past the login page.
- `workers_dev` and `preview_urls` are `false` in `wrangler.jsonc`: those default addresses
  would bypass the login gate. Keep them off.
- Access adds `cf-access-authenticated-user-email` to each request (`server/api/me.get.ts`).
  Anything that authorizes a data change must verify the `Cf-Access-Jwt-Assertion` JWT
  instead of trusting that header.

## Commands

```bash
bun install
bun run dev        # local dev server (no Access, so no signed-in email)
bun run preview    # build + run in Cloudflare's local runtime (wrangler dev)
bun run deploy     # build + deploy to Cloudflare (needs `bunx wrangler login` once)
```

## Settings

Server-only settings go in `runtimeConfig` in `nuxt.config.ts` and are read with
`useRuntimeConfig(event)`. On Cloudflare, set them as Worker variables/secrets named
`NUXT_<KEY>` (e.g. `NUXT_API_BASE_URL`); locally, in `service/web/.env`.
