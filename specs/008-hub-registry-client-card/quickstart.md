# Quickstart: validating spec 008

A run-through that proves the feature works end to end. Contracts: [hub-api.md](contracts/hub-api.md) · [ui-screens.md](contracts/ui-screens.md) · data model: [data-model.md](data-model.md).

## Prerequisites

- **Migration 010** applied (see `.claude/rules/backend/schema.md`), and parity verified: `script/verify_schema_parity.sh`.
- **`.env` has:**
  - `HUB_API_INTERNAL_TOKEN`
  - `SLACK_MANAGERIAL_ESKALA` and `SLACK_MANAGERIAL_NOKTAH` (new webhooks on the "Noktah Otomasi" Slack app)
  - `OPENROUTER_API_KEY` and the Google credentials, which already exist
- **Cloudflare Access:** the `hub.noktah.co` application policy is changed to *Include: Everyone* (G-14). `automate.noktah.co` keeps *Only managerials*.
- **Containers:** `docker-compose up -d --build api prefect prefect-worker`, then `docker exec prefect prefect --no-prompt deploy -n "hub-*"`.

## 1. Import the Registry (US4, SC-005)

```bash
docker exec prefect python flows/hub_registry_import.py --validate-only   # report only
docker exec prefect python flows/hub_registry_import.py                   # writes; pauses roster-sync
```

Expect **22 Clients plus Eskala (internal)**. The report lists "Nirwana Coffee Shop Sumenep" vs "…Space…", and SWA and SMEC Pekanbaru as created. People come from WORKERS, and the first roles are seeded (core@ Owner, bagas@ BM Venyu, defila@ BM Eskala). The `roster-sync` deployment shows as paused.

## 2. Roles (US1, SC-009)

- Sign in to `hub.noktah.co` as `defila@noktah.co`: you should see Eskala's Clients and the Persetujuan menu.
- Sign in with an email that has no role: you should see only "Anda belum punya akses". Every `/api/hub/v1/*` request made as that Person returns 403 `no_access`.
- As the Owner, grant a Project Manager role in Eskala to a test Person, then sign in as them. A Guideline edit shows "menunggu persetujuan", and `#eskala-managerial` gets the message.

## 3. Intake (US2, SC-001, SC-004)

On a Client, open **Intake** and paste:

```
[25/09/26 10.12] Bu Rina (PIC): Mulai 1 Oktober harga LASIK jadi 9,5 jt per mata ya, promo pelajar tetap. Tolong minggu depan bikin konten promo ini.
```

Expect:
- a Harga & promo Proposal (the excerpt is the sentence, valid from 2026-10-01)
- a Request Proposal

Accept both. The Profil shows the new price with its old value in history, and Permintaan shows the Request as *baru*. Then:
- Paste a message whose "quote" the AI can't find (e.g. edit the proposed excerpt check fixture): it is flagged *kutipan tidak ditemukan*.
- Upload a screenshot: its Proposals are marked *dari gambar — cek manual*, and *Terima* stays disabled until the tick is set.

## 4. Sheet copy (US4, SC-008)

Change a Client's Post quota in the Hub. Within 5 minutes the Clients tab shows it, and "Total Minutes Equivalent" and "No." are unchanged. `docker exec prefect python flows/hub_sheet_sync.py --check` reports zero differences.

## 5. Summary and cap (US3, SC-007)

```bash
docker exec prefect python flows/hub_summary_refresh.py
```

The Ringkasan tab shows "dibuat otomatis · tanggal". `GET /v1/ai/usage` shows the month's spend. To test the cap, temporarily set `HUB_AI_MONTHLY_CAP_USD=0.0001`: Intake then returns 402, the button is disabled, and a direct Profil edit still saves.

## 6. Old notes (US6)

```bash
docker exec prefect python flows/hub_notes_process.py --limit 10
```

Proposals appear on the matched Clients' Intake pages; unmatched notes appear in **Catatan lama**. Re-running skips notes already done.

## 7. UI sweep

```bash
cd service/web && bun run test:ui && bun run ui:gate
```

Expect zero findings across every page and state listed in `e2e/ui-sweep.e2e.ts`.

## 8. Tests

```bash
cd service/api && uv run pytest                      # pure tests
cd service/api && uv run pytest -m schema            # real-Postgres invariants
cd service/prefect && ./.venv/Scripts/python.exe -m pytest tests/ -m "not schema"
```
