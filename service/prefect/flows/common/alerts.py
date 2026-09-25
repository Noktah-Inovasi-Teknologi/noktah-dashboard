"""
Failure alerts to Slack's #<brand>-otomasi channels ("No silent failures").

Every scheduled flow reports a problem to Slack the moment its run ends, so a
broken job is noticed the next morning, not at month end when something is missing.

Attach with one line on the flow decorator:

    @flow(name="velocity-derive", **alert_hooks())

WHY A HOOK READS THE RESULT, NOT THE STATE: flows here never raise (see
.claude/rules/backend/prefect.md). They catch the exception and return it in
`result["error"]`, so Prefect records a broken run as COMPLETED. Prefect's own
failure notifications would miss nearly every real failure. The completion hook
therefore reads the returned dict. The failure/crash hooks cover the rest: an
exception that escaped anyway, or a worker that died mid-run.

Only problems are posted. A message for every healthy run would teach people
to ignore the channel, which defeats the point.

Webhooks come from a Slack app, "Noktah Otomasi", with incoming webhooks, one
per channel:
    SLACK_AUTOMATION_NOKTAH  -> #noktah-otomasi
    SLACK_AUTOMATION_ESKALA  -> #eskala-otomasi
    SLACK_AUTOMATION_VENYU   -> #venyu-otomasi
A missing variable logs a warning and posts nothing. An alert must never break
the run it reports on.
"""
import logging
import os
from typing import Any, Callable, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

CHANNEL_ENV = {
    "noktah": "SLACK_AUTOMATION_NOKTAH",
    "eskala": "SLACK_AUTOMATION_ESKALA",
    "venyu": "SLACK_AUTOMATION_VENYU",
}

# Where a person clicks through to the run. Set PREFECT_UI_PUBLIC_URL if the UI
# is ever exposed beyond the host machine.
DEFAULT_UI_URL = "http://localhost:4200"

MAX_ERROR_CHARS = 400


def find_problems(result: Any) -> List[str]:
    """Plain-Bahasa lines describing what went wrong in a flow's returned dict.

    Empty list = healthy run. Reads the shapes the flows actually return:
    a top-level `error`, songbird-batch's per-client `summary.failures`, and the
    harvest's blocked-profile / failed-item counts.
    """
    if not isinstance(result, dict):
        return []
    problems: List[str] = []

    error = result.get("error")
    if error:
        problems.append(f"Penyebab: {_clip(str(error))}")

    summary = result.get("summary") or {}
    if not isinstance(summary, dict):
        return problems

    failures = summary.get("failures")
    if isinstance(failures, dict) and failures:
        for name, reason in failures.items():
            problems.append(f"{name}: {_clip(str(reason), 200)}")

    blocked = summary.get("profiles_blocked") or 0
    if blocked:
        problems.append(
            f"{blocked} profil diblokir sementara oleh Instagram/TikTok, jadi kontennya belum terkumpul."
        )
        # Several blocks in one run usually mean the shared egress IP is
        # throttled, not the profiles: a fresh carrier IP fixes it.
        if blocked >= int(os.environ.get("HARVEST_ROTATE_IP_THRESHOLD", "2")):
            problems.append(
                "Kemungkinan IP server yang diblokir: ganti IP hotspot "
                "(matikan-nyalakan data seluler atau mode pesawat), lalu jalankan ulang."
            )
    failed_items = summary.get("items_failed") or 0
    if failed_items:
        problems.append(f"{failed_items} konten gagal diproses.")
    return problems


def build_message(
    flow_name: str,
    run_name: str,
    run_url: str,
    problems: List[str],
    crashed: bool = False,
    subject: Optional[str] = None,
) -> str:
    """The Slack text. ❌ when the run failed outright, ⚠️ when it finished with problems."""
    who = f" ({subject})" if subject else ""
    if crashed:
        head = f"❌ *{flow_name}*{who} gagal dan berhenti."
    else:
        head = f"⚠️ *{flow_name}*{who} selesai, tapi ada masalah."
    lines = [head, *(f"• {p}" for p in problems), f"Detail: <{run_url}|{run_name}>"]
    return "\n".join(lines)


async def post_to_slack(channel: str, text: str) -> bool:
    """POST to the channel's incoming webhook. Never raises; returns whether it was delivered."""
    env_name = CHANNEL_ENV.get(channel)
    url = os.environ.get(env_name or "")
    if not url:
        logger.warning(f"{env_name or channel} not set; alert not sent: {text[:120]}")
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url, json={"text": text})
        if response.status_code != 200:
            logger.warning(f"Slack alert to #{channel}-otomasi rejected: HTTP {response.status_code} {response.text[:200]}")
            return False
        return True
    except Exception as e:  # noqa: BLE001 - an alert must never break the run
        logger.warning(f"Slack alert to #{channel}-otomasi failed: {e}")
        return False


def alert_hooks(channel: str = "eskala") -> Dict[str, List[Callable]]:
    """`@flow(**alert_hooks())` — report problems to #<channel>-otomasi when the run ends."""
    if channel not in CHANNEL_ENV:
        raise ValueError(f"unknown alert channel {channel!r}; expected one of {sorted(CHANNEL_ENV)}")

    async def on_end(flow, flow_run, state) -> None:
        try:
            crashed = not state.is_completed()
            result = await state.result(raise_on_failure=False)
            if crashed:
                reason = result if isinstance(result, BaseException) else state.message
                problems = [f"Penyebab: {_clip(str(reason))}"] if reason else []
            else:
                problems = find_problems(result)
            if not problems:
                return
            text = build_message(
                flow_name=flow.name,
                run_name=flow_run.name,
                run_url=_run_url(flow_run.id),
                problems=problems,
                crashed=crashed,
                subject=_subject(flow_run.parameters or {}),
            )
            await post_to_slack(channel, text)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"alert hook failed for {getattr(flow, 'name', flow)}: {e}")

    return {"on_completion": [on_end], "on_failure": [on_end], "on_crashed": [on_end]}


def _subject(parameters: Dict[str, Any]) -> Optional[str]:
    """Which client(s) the run was for, so the alert says who is affected."""
    for key in ("client", "clients", "harvest_name"):
        value = parameters.get(key)
        if value:
            return _clip(str(value), 120)
    profiles = parameters.get("profiles")
    if isinstance(profiles, list) and profiles:
        handles = [str(p).rstrip("/").rsplit("/", 1)[-1] for p in profiles[:3]]
        more = f" +{len(profiles) - 3}" if len(profiles) > 3 else ""
        return ", ".join(handles) + more
    return None


def _run_url(flow_run_id: Any) -> str:
    base = os.environ.get("PREFECT_UI_PUBLIC_URL", DEFAULT_UI_URL).rstrip("/")
    return f"{base}/runs/flow-run/{flow_run_id}"


def _clip(text: str, limit: int = MAX_ERROR_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
