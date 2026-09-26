"""
/internal/*: jobs Prefect triggers on schedule (sheet copy, summaries, purge,
import). Hub logic stays in this one codebase; Prefect only decides WHEN.

Two locks, both required:
  1. `X-Hub-Internal-Token` must equal HUB_API_INTERNAL_TOKEN (constant-time
     compare). An empty configured token disables every internal route.
  2. The request must NOT have come through Cloudflare. Anything routed by the
     tunnel carries `cf-ray` / `cf-connecting-ip` (and Access adds
     `cf-access-jwt-assertion`); those requests are refused. So even a caller holding
     the Hub Worker's service token cannot reach /internal: only containers on the
     Docker network can.

Refusals answer 404, not 401/403, so the routes' existence isn't advertised.
"""
import hmac

from fastapi import APIRouter, Depends, Request

from .errors import NotFound
from .settings import Settings, get_settings

CLOUDFLARE_HEADERS = ("cf-ray", "cf-connecting-ip", "cf-access-jwt-assertion", "cf-ipcountry")


async def internal_only(request: Request, settings: Settings = Depends(get_settings)) -> None:
    if any(request.headers.get(h) for h in CLOUDFLARE_HEADERS):
        raise NotFound("Tidak ditemukan.")
    expected = settings.internal_token
    given = request.headers.get("x-hub-internal-token", "")
    if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
        raise NotFound("Tidak ditemukan.")


router = APIRouter(prefix="/internal", dependencies=[Depends(internal_only)])


@router.get("/ping")
async def ping() -> dict:
    """Lets a flow check it can reach the API with its token."""
    return {"ok": True}


@router.post("/summaries/refresh")
async def summaries_refresh() -> dict:
    """Hourly (flow hub-summary-refresh): refresh stale Summaries, at most once a day each (G-27)."""
    from . import db
    from .summary.service import refresh_all
    async with db.pool().acquire() as conn:
        return await refresh_all(conn)


@router.post("/intakes/purge-raw")
async def intakes_purge_raw() -> dict:
    """Daily (flow hub-intake-purge): raw pastes, screenshots and documents older than
    12 months are removed. The excerpts, Proposals and outcomes stay forever (FR-036),
    and so does the content hash, which keeps the cache from re-running them."""
    from . import db
    async with db.pool().acquire() as conn:
        purged = await conn.fetchval(
            """WITH purged AS (
                   UPDATE intakes SET raw_text = NULL, raw_blob = NULL, raw_purged_at = now()
                   WHERE raw_purged_at IS NULL AND submitted_at < now() - interval '12 months'
                     AND (raw_text IS NOT NULL OR raw_blob IS NOT NULL)
                   RETURNING 1)
               SELECT count(*) FROM purged""")
    return {"purged": purged}


async def _pause_roster_sync() -> str:
    """After the real import the Hub owns the roster: roster-sync must stop
    rewriting it from the sheet (R6). Paused through the Prefect API, never deleted."""
    import httpx

    base = get_settings().prefect_api_url.rstrip("/")
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"{base}/deployments/filter",
                              json={"deployments": {"name": {"any_": ["roster-sync"]}}})
        r.raise_for_status()
        found = r.json()
        if not found:
            return "not_found"
        for d in found:
            p = await client.patch(f"{base}/deployments/{d['id']}", json={"paused": True})
            p.raise_for_status()
    return "paused"


@router.post("/registry/import")
async def registry_import(validate_only: bool = True) -> dict:
    """Manual (flow hub-registry-import). validate_only defaults to TRUE: the real run
    is a deliberate second step, after a Manager reviewed the difference report."""
    import asyncio

    from . import db, google
    from .registry.importer import run_import
    from .registry.sheet_layout import CLIENTS_RANGE, HASHMAPS_RANGE

    ranges = await asyncio.to_thread(google.read_ranges, get_settings().clients_spreadsheet_id,
                                     [CLIENTS_RANGE, HASHMAPS_RANGE])
    async with db.pool().acquire() as conn:
        report = await run_import(conn, ranges[CLIENTS_RANGE], ranges[HASHMAPS_RANGE], validate_only)
    if validate_only:
        report["roster_sync"] = "would_pause"
    else:
        try:
            report["roster_sync"] = await _pause_roster_sync()
        except Exception as e:  # the import itself is committed; say loudly that roster-sync still runs
            report["roster_sync"] = f"pause_failed: {type(e).__name__}: {e}"
    return report


@router.post("/sheet-sync")
async def sheet_sync(check: bool = False, dry_run: bool = False) -> dict:
    """Every 5 min (flow hub-sheet-sync); Monday 06:00 with check=true."""
    from . import db
    from .registry.sheet_copy import sync
    async with db.pool().acquire() as conn:
        return await sync(conn, get_settings().clients_spreadsheet_id, check=check, dry_run=dry_run)


# ── Otomasi & Laporan (spec 009): flows' side ────────────────────────────────
from .automation.internal import router as _automation_internal  # noqa: E402
from .incentive.routes import internal as _incentive_internal  # noqa: E402
from .reports.internal import router as _reports_internal  # noqa: E402

router.include_router(_automation_internal)
router.include_router(_reports_internal)
router.include_router(_incentive_internal)
