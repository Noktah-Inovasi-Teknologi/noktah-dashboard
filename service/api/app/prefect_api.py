"""
The Prefect API, over the Docker network (spec 009, research R2).

hub-api starts the Otomasi flows when a Manager presses a button ("Buat issue Jira",
"Jalankan sekarang", "Periksa ulang") and reads recent runs for the Otomasi cards. The flows
do the outside work; this only asks Prefect to run them. Nothing here schedules anything.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from .errors import HubError
from .settings import get_settings

# "<flow name>/<deployment name>", as in service/prefect/prefect.yaml
PLAN_WATCH = "hub-plan-watch/hub-plan-watch"
JIRA_CREATE = "hub-jira-create/hub-jira-create"
JIRA_SYNC = "hub-jira-sync/hub-jira-sync"
HARVEST = "harvest-registry/harvest-registry"
SANCTIONS = "hub-sanctions/hub-sanctions"


class PrefectUnavailable(HubError):
    # 424, not 5xx: the web proxy turns any 5xx into "the office PC is off", which would be wrong here.
    status, code = 424, "automation_unavailable"


def _base() -> str:
    return get_settings().prefect_api_url.rstrip("/")


async def run_deployment(name: str, parameters: Dict[str, Any]) -> str:
    """Start one run of a deployment now; returns the flow run id."""
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            d = await client.get(f"{_base()}/deployments/name/{name}")
            d.raise_for_status()
            r = await client.post(f"{_base()}/deployments/{d.json()['id']}/create_flow_run",
                                  json={"parameters": parameters})
            r.raise_for_status()
            return r.json()["id"]
    except (httpx.HTTPError, KeyError) as e:
        raise PrefectUnavailable(
            "Otomasi tidak bisa dijalankan: penjadwal otomasi di PC kantor tidak menjawab. Coba lagi beberapa menit lagi.",
            detail=f"{type(e).__name__}: {e}")


def _run(r: Dict[str, Any], deployment: str) -> Dict[str, Any]:
    state = r.get("state") or {}
    return {"id": r.get("id"), "name": r.get("name"), "deployment": deployment,
            "state": state.get("type"), "state_name": state.get("name"),
            "started_at": r.get("start_time") or r.get("expected_start_time"),
            "ended_at": r.get("end_time")}


async def runs_for(names: List[str], days: int = 90) -> Optional[Dict[str, Any]]:
    """Recent runs (newest first) and the next scheduled run across `names`.
    None when Prefect can't be reached: the Otomasi page says so instead of failing."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            ids: Dict[str, str] = {}
            for name in names:
                d = await client.get(f"{_base()}/deployments/name/{name}")
                if d.status_code == 200:
                    ids[d.json()["id"]] = name.split("/")[-1]
            if not ids:
                return {"history": [], "next_run_at": None}
            flt = {"deployments": {"id": {"any_": list(ids)}}}
            past = await client.post(f"{_base()}/flow_runs/filter", json={
                "deployments": flt["deployments"],
                "flow_runs": {"start_time": {"after_": since},
                              "state": {"type": {"not_any_": ["SCHEDULED"]}}},
                "sort": "START_TIME_DESC", "limit": 200})
            past.raise_for_status()
            nxt = await client.post(f"{_base()}/flow_runs/filter", json={
                "deployments": flt["deployments"],
                "flow_runs": {"state": {"type": {"any_": ["SCHEDULED"]}}},
                "sort": "EXPECTED_START_TIME_ASC", "limit": 1})
            nxt.raise_for_status()
    except (httpx.HTTPError, KeyError, ValueError):
        return None
    history = [_run(r, ids.get(r.get("deployment_id"), "")) for r in past.json()]
    upcoming = nxt.json()
    return {"history": history,
            "next_run_at": upcoming[0].get("expected_start_time") if upcoming else None}
