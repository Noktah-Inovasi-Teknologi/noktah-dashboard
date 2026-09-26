"""
Calls from Prefect to hub-api's /internal routes (spec 008).

Hub logic lives in hub-api; these flows only decide WHEN it runs. The API is on
the Docker network at HUB_API_URL (http://api:8000) and accepts /internal only
with HUB_API_INTERNAL_TOKEN, and never through Cloudflare.
"""
import os
from typing import Any, Dict, Optional

import httpx
from prefect import task


def _base_and_token() -> tuple[str, str]:
    base = os.environ.get("HUB_API_URL", "http://api:8000").rstrip("/")
    token = os.environ.get("HUB_API_INTERNAL_TOKEN", "")
    if not token:
        raise RuntimeError("HUB_API_INTERNAL_TOKEN is not set; hub-api internal routes are unreachable")
    return base, token


@task(name="hub.internal.call", retries=2, retry_delay_seconds=30)
def hub_internal_call(path: str, params: Optional[Dict[str, Any]] = None, timeout: float = 600.0,
                      json: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """POST /internal/<path>; raises on non-2xx so Prefect retries and the flow alerts.

    `params` go in the query string (the spec 008 routes); `json` is the request body
    (the spec 009 routes, whose inputs are lists of rows, issues and results).
    """
    base, token = _base_and_token()
    response = httpx.post(f"{base}/internal/{path.lstrip('/')}", params=params or {}, json=json,
                          headers={"X-Hub-Internal-Token": token}, timeout=timeout)
    if response.status_code == 404:
        raise RuntimeError(f"hub-api refused /internal/{path} (token mismatch, route missing, or "
                           f"not found): {response.text[:300]}")
    if response.status_code >= 400:
        raise RuntimeError(f"hub-api /internal/{path} answered HTTP {response.status_code}: {response.text[:300]}")
    return response.json()
