"""
Shared Songbird content-generation engine (feature 003-songbird-content-generation).

Assembles the "hit" signals (client knowledge + own/competitor top performers +
marketing params), builds an Indonesian/code-mixed prompt, calls OpenRouter for a
structured batch of content ideas, assigns publish dates (monthly plan only), and
delivers a draft or live content-plan worksheet. Shared by both flows:
`songbird_monthly_plan` (dated, draft|live) and `songbird_generate` (standalone,
draft-only). This module never raises to its caller — it always returns a
Dict[str, Any] with start_time/end_time/data/summary/error (constitution I) and
sets end_time in a finally block (constitution V).
"""
import calendar
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from prefect.logging import get_run_logger

try:
    from ...tasks.songbird_tasks import (
        songbird_client_context,
        songbird_config_quantity,
        songbird_top_performers,
    )
    from ...tasks.openrouter_tasks import array_schema, openrouter_chat
    from ...tasks.google_tasks import (
        drive_folder_ensure,
        google_read_sheet_data,
        sheets_create,
        sheets_rows_append,
    )
    from ...hashmap import CLIENT_SOCIAL
except ImportError:
    import sys

    sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    from tasks.songbird_tasks import (
        songbird_client_context,
        songbird_config_quantity,
        songbird_top_performers,
    )
    from tasks.openrouter_tasks import array_schema, openrouter_chat
    from tasks.google_tasks import (
        drive_folder_ensure,
        google_read_sheet_data,
        sheets_create,
        sheets_rows_append,
    )
    from hashmap import CLIENT_SOCIAL

logger = logging.getLogger(__name__)

# Machine + human caveat that a "hit" is a bias, not a guarantee (FR-006).
HIT_DISCLAIMER = "Performa audiens adalah bias dari pola historis dan tidak dijamin."

# Columns the downstream content-plan → Jira flow reads
# (convert_content_plan_row_to_jira_issue). Order is the draft/live column order.
CONTENT_PLAN_COLUMNS = [
    "Topik", "Tanggal", "Bentuk", "Format",
    "Purpose/Theme", "Strategic Application", "Visualisasi Konten",
]
# Reviewer-only columns appended in the draft; never written to the live sheet (FR-016a).
RATIONALE_COLUMNS = ["Adapted Pattern", "Source Exemplar", "Rationale", "Hit Note"]
DRAFT_HEADER = CONTENT_PLAN_COLUMNS + RATIONALE_COLUMNS

# Required keys per generated idea (strict json_schema — contracts/generation-io.md).
IDEA_KEYS = [
    "topik", "bentuk", "format", "purpose_theme", "strategic_application",
    "visualisasi_konten", "adapted_pattern", "source_exemplar", "rationale",
]

# Indonesian (and English) month names → month number, for "Agustus 2026" parsing.
_MONTHS = {
    "januari": 1, "february": 2, "februari": 2, "january": 1, "march": 3, "maret": 3,
    "april": 4, "may": 5, "mei": 5, "june": 6, "juni": 6, "july": 7, "juli": 7,
    "august": 8, "agustus": 8, "september": 9, "october": 10, "oktober": 10,
    "november": 11, "nopember": 11, "december": 12, "desember": 12,
}

# Fallback for the live content-plan worksheet (same workbook the content-plan flow reads).
DEFAULT_LIVE_SPREADSHEET_ID = os.environ.get(
    "SONGBIRD_LIVE_SPREADSHEET_ID", "1-aV46TIn4m_zs3vtCNeS_Bvl3Tt-tgg09uuG_NqgNNY"
)
DEFAULT_LIVE_TAB = os.environ.get("SONGBIRD_LIVE_TAB", "Clients")


def _parse_month(month_str: str) -> tuple[int, int]:
    """Parse 'Agustus 2026' / 'August 2026' → (year, month). Raises ValueError."""
    parts = month_str.strip().split()
    if len(parts) != 2:
        raise ValueError(f"Invalid month '{month_str}'; expected 'Month YYYY' (e.g. 'Agustus 2026')")
    name, year = parts
    month = _MONTHS.get(name.strip().lower())
    if month is None or not year.isdigit():
        raise ValueError(f"Unrecognized month '{month_str}'")
    return int(year), month


def _distribute_dates(year: int, month: int, quantity: int) -> List[str]:
    """Evenly spread `quantity` publish dates across the month (FR-004, R7).

    Deterministic even spacing over the month's days; when quantity exceeds the
    day count, dates repeat but stay spread rather than collapsing to one day.
    """
    days_in_month = calendar.monthrange(year, month)[1]
    dates: List[str] = []
    for i in range(quantity):
        # Spread indices 0..quantity-1 across 1..days_in_month.
        day = 1 + round(i * (days_in_month - 1) / max(quantity - 1, 1))
        day = min(max(day, 1), days_in_month)
        dates.append(f"{year:04d}-{month:02d}-{day:02d}")
    return dates


def _resolve_handles(
    client: str, own_handles: Optional[List[str]], competitor_handles: Optional[List[str]]
) -> tuple[List[str], List[str]]:
    """Merge CLIENT_SOCIAL config with per-run handle overrides (R6)."""
    cfg = CLIENT_SOCIAL.get(client, {})
    own = list(dict.fromkeys((cfg.get("own") or []) + (own_handles or [])))
    competitors = list(dict.fromkeys((cfg.get("competitors") or []) + (competitor_handles or [])))
    return own, competitors


def _performer_brief(performers: List[Dict[str, Any]], label: str) -> str:
    """Condense ranked performers into prompt-friendly exemplar lines."""
    lines = []
    for p in performers:
        eng = (p.get("likes") or 0) + (p.get("comments") or 0)
        flow = (p.get("content_flow") or "").strip().replace("\n", " ")
        summary = (p.get("summary") or "").strip().replace("\n", " ")
        lines.append(
            f"- [{label}] @{p.get('profile_key')} ({p.get('content_type')}, ~{eng} interaksi): "
            f"alur: {flow[:300]} | ringkasan: {summary[:300]}"
        )
    return "\n".join(lines)


def _build_prompt(
    client_name: str,
    records: List[Dict[str, Any]],
    own_perf: List[Dict[str, Any]],
    comp_perf: List[Dict[str, Any]],
    params: Dict[str, Any],
    quantity: int,
) -> tuple[str, str]:
    """Assemble the (system, user) prompt (contracts/generation-io.md, R9)."""
    system = (
        "Anda adalah content strategist berpengalaman untuk brand Indonesia. "
        "Tulis ide konten dalam Bahasa Indonesia yang natural, dengan code-mixing "
        "Bahasa Inggris seperlunya (nama brand, hashtag, istilah baku seperti 'reels'/"
        "'engagement', frasa/tagline yang sedang tren) — JANGAN menerjemahkan paksa istilah "
        "tersebut. Tiru gaya code-mixing yang terlihat pada knowledge base klien dan konten "
        "yang di-harvest. Adaptasi POLA yang terbukti perform (hook, format, pacing), "
        "JANGAN menyalin konten contoh secara verbatim. Performa tidak dijamin."
    )

    kb = "\n".join(f"- {r['subject']}: {r['information']}" for r in records) or "(tidak ada data KB)"
    exemplars = "\n".join(
        s for s in [_performer_brief(own_perf, "milik klien"), _performer_brief(comp_perf, "kompetitor")] if s
    ) or "(belum ada sinyal konten yang di-harvest — bertumpu pada KB + parameter)"

    pillars = ", ".join(params.get("content_pillars") or []) or "(bebas, sesuai brand)"
    user = (
        f"Klien: {client_name}\n"
        f"Platform: {params.get('platform') or '(umum)'}\n"
        f"Target audiens: {params.get('audience') or '(umum)'}\n"
        f"Tujuan kampanye: {params.get('goal') or '(brand awareness)'}\n"
        f"Tone: {params.get('tone') or '(sesuai brand)'}\n"
        f"Content pillars: {pillars}\n\n"
        f"Pengetahuan brand (knowledge base):\n{kb}\n\n"
        f"Konten yang terbukti perform (pelajari polanya, jangan salin):\n{exemplars}\n\n"
        f"Buatkan TEPAT {quantity} ide konten yang berbeda-beda. Untuk setiap ide isi: "
        f"topik, bentuk (mis. Reels/Feed/Carousel/Story), format, purpose_theme, "
        f"strategic_application, visualisasi_konten (catatan eksekusi visual), "
        f"adapted_pattern (pola menang yang diadaptasi, atau 'umum' bila tanpa sinyal), "
        f"source_exemplar (referensi handle/konten sumber, kosong bila tanpa sinyal), "
        f"dan rationale (alasan cocok untuk klien + tujuan). "
        f"JANGAN menyertakan tanggal."
    )
    return system, user


def _idea_cells(idea: Dict[str, Any], date_str: str) -> Dict[str, str]:
    """Map one generated idea (+ assigned date) to a {column_name: cell} dict."""
    return {
        "Topik": idea.get("topik", ""),
        "Tanggal": date_str,
        "Bentuk": idea.get("bentuk", ""),
        "Format": idea.get("format", ""),
        "Purpose/Theme": idea.get("purpose_theme", ""),
        "Strategic Application": idea.get("strategic_application", ""),
        "Visualisasi Konten": idea.get("visualisasi_konten", ""),
        "Adapted Pattern": idea.get("adapted_pattern", ""),
        "Source Exemplar": idea.get("source_exemplar", ""),
        "Rationale": idea.get("rationale", ""),
        "Hit Note": HIT_DISCLAIMER,
    }


async def run_generation(
    client: str,
    quantity: Optional[int] = None,
    *,
    distribute_dates: bool = False,
    resolve_quantity_from_config: bool = False,
    month: Optional[str] = None,
    target: str = "draft",
    platform: str = "",
    audience: str = "",
    goal: str = "",
    tone: str = "",
    content_pillars: Optional[List[str]] = None,
    signal_window_days: int = 180,
    own_handles: Optional[List[str]] = None,
    competitor_handles: Optional[List[str]] = None,
    live_spreadsheet_id: Optional[str] = None,
    live_tab: Optional[str] = None,
    exemplar_limit: int = 8,
    credentials_block_name: str = "google-creds",
) -> Dict[str, Any]:
    """
    Generate a batch of targeted content ideas and deliver them (draft or live).

    Args:
        client: Client name (grounds KB lookup, handle resolution, config quantity).
        quantity: Number of ideas; if None and resolve_quantity_from_config, read
            from the Clients worksheet (FR-003b).
        distribute_dates: Assign month-distributed publish dates (monthly plan only).
        resolve_quantity_from_config: Resolve quantity from the Clients worksheet.
        month: Target month "Month YYYY" (required when distribute_dates).
        target: "draft" (default) or "live" (monthly only) — FR-015.
        platform/audience/goal/tone/content_pillars: marketing params (FR-003).
        signal_window_days: Rolling recency window for top performers (FR-009a).
        own_handles/competitor_handles: per-run handle overrides (merged w/ CLIENT_SOCIAL).
        live_spreadsheet_id/live_tab: live content-plan worksheet target.
        exemplar_limit: Max top-performer exemplars per group.
        credentials_block_name: Google credentials block name.

    Returns:
        Standard Dict with start_time, end_time, data, summary, optional error.
        Never raises (constitution I).
    """
    run_logger = get_run_logger()
    start_time = datetime.now(timezone.utc)
    data: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {
        "client": client,
        "ideas_requested": quantity,
        "ideas_produced": 0,
        "ideas_failed": 0,
        "signal_available": False,
        "target": "draft",
        "deliverable_id": None,
        "hit_disclaimer": HIT_DISCLAIMER,
    }
    error: Optional[str] = None
    end_time = start_time

    try:
        # 1. Resolve quantity (fail fast per FR-003b when required + unconfigured).
        if quantity is None and resolve_quantity_from_config:
            quantity = await songbird_config_quantity(client, credentials_block_name=credentials_block_name)
        if not quantity or quantity <= 0:
            raise ValueError("No positive content quantity resolved for this run")
        summary["ideas_requested"] = quantity

        # 2. Dates (monthly only) — computed here, never by the model (FR-004).
        if distribute_dates:
            if not month:
                raise ValueError("month is required when distribute_dates is set")
            year, month_num = _parse_month(month)
            dates = _distribute_dates(year, month_num, quantity)
        else:
            dates = [""] * quantity

        # 3. Gather signals.
        own, competitors = _resolve_handles(client, own_handles, competitor_handles)
        context = await songbird_client_context(client)
        records = context.get("records", [])
        client_name = context.get("client_name", client)
        own_perf = await songbird_top_performers(own, limit=exemplar_limit, window_days=signal_window_days) if own else []
        comp_perf = await songbird_top_performers(competitors, limit=exemplar_limit, window_days=signal_window_days) if competitors else []
        summary["signal_available"] = bool(own_perf or comp_perf)
        if not summary["signal_available"]:
            run_logger.warning(f"{client}: no harvested signal available — generating from KB + params only (FR-011)")
        if not records:
            run_logger.warning(f"{client}: no knowledge-base records found — grounding reduced")

        # 4. Generate.
        params = {"platform": platform, "audience": audience, "goal": goal, "tone": tone, "content_pillars": content_pillars}
        system, user = _build_prompt(client_name, records, own_perf, comp_perf, params, quantity)
        max_tokens = min(400 * quantity + 500, 16000)
        result = await openrouter_chat(
            user=user, system=system,
            response_format=array_schema("content_ideas", IDEA_KEYS),
            max_tokens=max_tokens,
        )
        raw_ideas = result.get("items", []) if isinstance(result, dict) else []

        # 5. Validate + shape ideas (drop malformed, continue — FR-020).
        ideas: List[Dict[str, Any]] = []
        for item in raw_ideas:
            if isinstance(item, dict) and str(item.get("topik", "")).strip() and str(item.get("bentuk", "")).strip():
                ideas.append(item)
            else:
                run_logger.warning(f"{client}: dropped a malformed idea (missing topik/bentuk)")
        ideas = ideas[:quantity]
        # Single failure tally: every requested idea not delivered counts once
        # (covers both malformed drops and a short model return) — SC-007.
        summary["ideas_failed"] = max(quantity - len(ideas), 0)
        if summary["ideas_failed"]:
            run_logger.warning(f"{client}: delivered {len(ideas)}/{quantity} usable ideas")

        # 6. Build rows.
        rows_cells = [_idea_cells(idea, dates[i] if i < len(dates) else "") for i, idea in enumerate(ideas)]
        if not rows_cells:
            raise ValueError("No usable content ideas were generated")

        # 7. Deliver.
        effective_target = target if distribute_dates else "draft"
        summary["target"] = effective_target
        if effective_target == "live":
            deliverable_id = await _deliver_live(
                rows_cells, live_spreadsheet_id, live_tab, credentials_block_name, run_logger
            )
        else:
            deliverable_id = await _deliver_draft(
                client_name, month, distribute_dates, rows_cells, credentials_block_name, run_logger
            )
        summary["deliverable_id"] = deliverable_id

        data = [{"tanggal": c["Tanggal"], "topik": c["Topik"], "bentuk": c["Bentuk"],
                 "adapted_pattern": c["Adapted Pattern"]} for c in rows_cells]
        summary["ideas_produced"] = len(data)
        run_logger.info(
            f"{client}: delivered {len(data)} ideas to {effective_target} (signal={summary['signal_available']})"
        )

    except Exception as e:
        run_logger.error(f"Songbird generation failed for {client}: {e}")
        error = str(e)
    finally:
        end_time = datetime.now(timezone.utc)

    return {
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "data": data,
        "summary": summary,
        "error": error,
    }


async def _deliver_draft(
    client_name: str, month: Optional[str], dated: bool,
    rows_cells: List[Dict[str, str]], credentials_block_name: str, run_logger,
) -> str:
    """Create a draft Sheet (content-plan + rationale columns) and append rows (FR-016a)."""
    parent_id = os.environ["SONGBIRD_DRIVE_PARENT_ID"]
    folder_id = await drive_folder_ensure("Songbird Drafts", parent_id, credentials_block_name=credentials_block_name)
    if dated and month:
        title = f"{client_name} — Songbird Draft {month}"
    else:
        title = f"{client_name} — Songbird Ideas {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S')}"
    sheet_id = await sheets_create(title, folder_id, header_row=DRAFT_HEADER, credentials_block_name=credentials_block_name)
    rows = [[cells.get(col, "") for col in DRAFT_HEADER] for cells in rows_cells]
    await sheets_rows_append(sheet_id, rows, sheet_name="Sheet1", credentials_block_name=credentials_block_name)
    run_logger.info(f"Draft delivered: '{title}' -> {sheet_id}")
    return sheet_id


async def _deliver_live(
    rows_cells: List[Dict[str, str]], live_spreadsheet_id: Optional[str], live_tab: Optional[str],
    credentials_block_name: str, run_logger,
) -> str:
    """Append rows to the live content-plan worksheet, aligned to its header by name (FR-016)."""
    spreadsheet_id = live_spreadsheet_id or DEFAULT_LIVE_SPREADSHEET_ID
    tab = live_tab or DEFAULT_LIVE_TAB
    info = await google_read_sheet_data(spreadsheet_id, tab, credentials_block_name=credentials_block_name)
    header = (info.get("dataframe_info") or {}).get("columns") or []
    if not header:
        raise ValueError(f"Live worksheet '{tab}' ({spreadsheet_id}) has no readable header row")
    # Log content-plan columns absent from the live sheet (never fabricate them).
    missing = [c for c in CONTENT_PLAN_COLUMNS if c not in header]
    if missing:
        run_logger.warning(f"Live worksheet missing content-plan columns {missing}; those cells left blank")
    # Align by column NAME; rationale columns are excluded (not in content-plan header set).
    rows = [[cells.get(col, "") if col in CONTENT_PLAN_COLUMNS else "" for col in header] for cells in rows_cells]
    await sheets_rows_append(spreadsheet_id, rows, sheet_name=tab, credentials_block_name=credentials_block_name)
    run_logger.info(f"Live delivered: {len(rows)} rows appended to '{tab}' ({spreadsheet_id})")
    return spreadsheet_id
