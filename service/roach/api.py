"""FastAPI app exposing roach's scraping primitives to the Prefect harvest flows.

Internal-only service (dashboard-networks). Every endpoint except /health requires
the X-API-KEY header to match ROACH_API_KEY. Roach is stateless per call — the
Prefect flow owns the loop, pacing, hourly cap, back-off, dedupe, and delivery.
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import analyze as analyze_mod
import collect


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Log a warning for any missing cookie session so silent auth gaps (e.g. IG
    # listing only surfacing a few posts) are diagnosable at boot.
    collect.validate_secrets()
    yield


app = FastAPI(title="roach", description="Instagram/TikTok content scraping API", lifespan=_lifespan)


def _require_api_key(x_api_key: str | None) -> None:
    expected = os.environ.get("ROACH_API_KEY")
    if not expected or x_api_key != expected:
        raise HTTPException(status_code=401, detail={"ok": False, "code": "unauthorized", "reason": "invalid or missing X-API-KEY"})


class ListRequest(BaseModel):
    profile_url: str
    platform: str
    max_items: int = 30
    stories_only: bool = False


class DownloadRequest(BaseModel):
    content_id: str
    source_url: str
    is_video: bool
    content_type: str


class AnalyzeRequest(BaseModel):
    content_id: str
    local_paths: list[str]
    content_type: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/list")
def list_profile(req: ListRequest, x_api_key: str | None = Header(default=None)):
    _require_api_key(x_api_key)
    try:
        profile, items = collect.list_profile(req.profile_url, req.platform, req.max_items, req.stories_only)
    except collect.ProfileNotFoundError as e:
        return JSONResponse(status_code=404, content={"ok": False, "code": "profile_not_found", "reason": str(e)})
    except collect.RateLimitedError as e:
        return JSONResponse(status_code=429, content={"ok": False, "code": e.code, "reason": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "code": "list_failed", "reason": str(e)})
    return {"ok": True, "profile": profile, "items": items}


@app.post("/download")
def download_item(req: DownloadRequest, x_api_key: str | None = Header(default=None)):
    _require_api_key(x_api_key)
    try:
        local_paths, resolved_content_type = collect.download_item(
            content_id=req.content_id,
            source_url=req.source_url,
            is_video=req.is_video,
            content_type=req.content_type,
        )
    except collect.ContentGoneError as e:
        return JSONResponse(status_code=404, content={"ok": False, "code": "content_gone", "reason": str(e)})
    except collect.RateLimitedError as e:
        return JSONResponse(status_code=429, content={"ok": False, "code": e.code, "reason": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"ok": False, "code": "download_failed", "reason": str(e)})
    # content_type is re-derived from the downloaded files (authoritative) — the
    # flow uses it for folder routing, analysis model, and the sheet row.
    return {
        "ok": True,
        "content_id": req.content_id,
        "local_paths": local_paths,
        "content_type": resolved_content_type,
    }


@app.post("/analyze")
def analyze_item(req: AnalyzeRequest, x_api_key: str | None = Header(default=None)):
    _require_api_key(x_api_key)
    result = analyze_mod.analyze_item(req.local_paths, req.content_type)
    return {"ok": True, "content_id": req.content_id, "analysis": result}
