# Noktah Hub API

The Python (FastAPI) service behind **Noktah Hub** (`hub.noktah.co`, code in `service/web`).
It is the only service that writes company data for the Hub, and later it will run the
AI intake. The Hub itself never touches the database or OpenRouter.

## How a request reaches it

```
manager ─login─▶ hub.noktah.co (Cloudflare Worker, Access app "Noktah Hub")
                   │  + service token noktah-hub-worker
                   │  + the manager's Access JWT as X-Hub-User-Jwt
                   ▼
               hub-api.noktah.co (Access app "Noktah Hub API", policy "Hub Worker only")
                   ▼  Cloudflare tunnel
               container hub-api:8000 ─▶ Postgres (noktah_dashboard)
```

- **No published port.** The container is reachable only through the tunnel, never from
  the office network.
- **Two proofs per request** (`app/auth.py`): the Access JWT for this API's application,
  which shows the call came through Access with the Hub's service token, and the manager's
  own Hub login JWT, which shows *who* is acting. Both are verified against the Access
  team's signing keys (signature, audience, issuer, expiry). A plain email header is never
  trusted.
- **Least privilege.** docker-compose gives it only `HUB_API_DATABASE_URL` and the AUD tag,
  not the whole `.env`.

## Settings (env, prefix `HUB_API_`)

| Variable | Meaning |
|---|---|
| `HUB_API_DATABASE_URL` | Postgres DSN |
| `HUB_API_ACCESS_API_AUD` | AUD tag of the "Noktah Hub API" Access application (required, not secret) |
| `HUB_API_ACCESS_HUB_AUD` | AUD tag of the "Noktah Hub" application (defaulted in `settings.py`) |
| `HUB_API_ACCESS_TEAM_DOMAIN` | `shy-bird-c2c5.cloudflareaccess.com` (defaulted) |

## Commands

```bash
uv sync && uv run pytest              # tests (no network, no database)
docker-compose up -d --build api      # rebuild + restart after code changes
docker logs -f hub-api
```
