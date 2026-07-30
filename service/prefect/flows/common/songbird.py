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
import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from prefect.logging import get_run_logger

try:
    from ...tasks.songbird_tasks import (
        songbird_client_context,
        songbird_config_content_mix,
        songbird_config_draft_folder,
        songbird_config_own_handles,
        songbird_top_performers,
    )
    from ...tasks.songbird_ranking import (
        _BENTUK_SYNONYMS as _RANKING_SYNONYMS,
        RECENCY_HALF_LIFE_DAYS,
        canonical_bentuk,
    )
    from ...tasks.songbird_themes import (
        allocate_slots,
        songbird_theme_induction,
        themes_from_pillars,
    )
    from ...tasks.songbird_trends import detect_trends, format_trends
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
        songbird_config_content_mix,
        songbird_config_draft_folder,
        songbird_config_own_handles,
        songbird_top_performers,
    )
    from tasks.songbird_ranking import (
        _BENTUK_SYNONYMS as _RANKING_SYNONYMS,
        RECENCY_HALF_LIFE_DAYS,
        canonical_bentuk,
    )
    from tasks.songbird_themes import (
        allocate_slots,
        songbird_theme_induction,
        themes_from_pillars,
    )
    from tasks.songbird_trends import detect_trends, format_trends
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

# The content-plan draft layout ("DRAFT v5"), matched column-for-column and in order.
# The downstream Jira converter reads Topik/Tanggal/Bentuk (+ Format, Purpose/Theme,
# Strategic Application, Shoot Guide), all of which are present here.
CONTENT_PLAN_COLUMNS = [
    "No.", "Tanggal", "Waktu", "Bentuk", "Topik", "Creator", "Format",
    "Purpose/Theme", "Strategic Application", "Kebutuhan Personil", "Known Facts",
    "Shoot Guide", "Reference", "Asset", "Caption", "Keterangan", "Approval",
    "Link Referensi", "TicketID", "Key",
]

# Columns songbird fills; everything else in the layout is deliberately left blank for
# the production workflow (scheduling, personnel, assets, approval, ticketing).
GENERATED_COLUMNS = {
    "No.", "Tanggal", "Bentuk", "Topik", "Creator", "Format",
    "Purpose/Theme", "Strategic Application", "Shoot Guide", "Reference", "Caption",
}

# `Creator` is a fixed value in the v5 plans — content originates from the brand.
DEFAULT_CREATOR = "Brand"

# Content types used when no per-type mix is configured (on-demand runs), and the
# fallback when a model-chosen `bentuk` can't be recognised.
DEFAULT_CONTENT_TYPES = ["Post", "Story", "Short Video"]

# Per-content-type briefs for `shoot_guide` / `reference`, written to reproduce how the
# v5 plans actually fill those two columns: Posts carry a slide-by-slide carousel design
# and no shoot guide; Story/Short Video carry a scene-by-scene capture plan.
_FORMAT_BRIEFS = {
    "Post": (
        "- shoot_guide: isi tanda '-' (Post tidak butuh pengambilan footage).\n"
        "- reference: rancang sebagai CAROUSEL, tulis slide demi slide dengan struktur "
        "'SLIDE n: <judul>' lalu 'Visual:', 'Headline:', 'Body Text:', dan CTA. Slide 1 = hook "
        "utama; SLIDE 2 HARUS berdiri sendiri sebagai hook kedua, karena Instagram menyajikan "
        "ulang carousel dengan slide 2 di depan bagi yang belum swipe. Slide terakhir = CTA.\n"
    ),
    "Story": (
        "- shoot_guide: rencana pengambilan NYATA per scene ('Scene n (x detik): ...') — shot, "
        "angle, blocking, dan tekankan ambience nyata yang perlu direkam (lokasi, cahaya, mood, "
        "b-roll, tekstur) agar tidak terasa 100% AI.\n"
        "- reference: alur Story antar-frame + elemen interaktif (polling/kuis/sticker) bila cocok.\n"
    ),
    "Short Video": (
        "- shoot_guide: KONSEP + durasi + talent, lalu breakdown per scene "
        "('SCENE n: <nama> (Lokasi - x detik)') dengan Shot, Blocking, teks overlay, dan pacing. "
        "Scene 1 wajib hook 3 detik pertama.\n"
        "- reference: link konten referensi bila ada, atau deskripsi alur/gaya editing.\n"
    ),
}
# Fallback brief for any content type outside the standard three.
_DEFAULT_FORMAT_BRIEF = (
    "- shoot_guide: panduan pengambilan footage nyata bila relevan, selain itu '-'.\n"
    "- reference: alur konten atau referensi visual.\n"
)
# The draft matches the v5 layout exactly, so reviewers see the same sheet they always
# do. The "why" behind each idea (adapted pattern, source exemplar, rationale) and the
# hit disclaimer are still captured — in the run outcome JSON rather than as extra
# columns, so FR-005/FR-006 are satisfied without deviating from the plan format.
RATIONALE_COLUMNS: List[str] = []
DRAFT_HEADER = CONTENT_PLAN_COLUMNS

# Required keys per generated idea (strict json_schema — contracts/generation-io.md).
# `shoot_guide` = how to capture real footage (video/story only; "-" for posts);
# `reference` = the slide-by-slide design flow for posts, or a reference for videos;
# `caption` = the ready-to-post caption. `bentuk` is NOT requested — generation is
# per content type, so the engine already knows it and asking wastes tokens.
IDEA_KEYS = [
    "topik", "purpose_theme", "strategic_application",
    "shoot_guide", "reference", "caption",
    "adapted_pattern", "source_exemplar", "rationale",
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

# Exemplar budget ceiling. Each exemplar costs ~800 chars of prompt, so 24 keeps the
# grounding block around 5k input tokens even for a large monthly plan.
MAX_EXEMPLARS = 24
# Share of the exemplar budget reserved for the client's own content; the remainder
# (including anything own doesn't use) goes to competitors.
OWN_EXEMPLAR_SHARE = 0.4
# Multiplier on the exemplar budget when a client has no knowledge base: harvested
# content is then the sole source of brand voice, so the model gets more of it.
NO_KB_EXEMPLAR_BOOST = 1.5


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
    client: str,
    own_handles: Optional[List[str]],
    competitor_handles: Optional[List[str]],
    sheet_own: Optional[List[str]] = None,
) -> tuple[List[str], List[str]]:
    """
    Merge handle sources (R6): the Hashmaps CLIENT_SOCIAL block (competitors), the
    Clients worksheet's own profile URLs (FR-008), and per-run overrides.
    """
    cfg = CLIENT_SOCIAL.get(client, {})
    own = list(dict.fromkeys(
        (cfg.get("own") or []) + (sheet_own or []) + (own_handles or [])
    ))
    competitors = list(dict.fromkeys((cfg.get("competitors") or []) + (competitor_handles or [])))
    return own, competitors


# The bentuk vocabulary is shared with the ranking module, which buckets harvested
# `content_type` values through the same map — one mapping, so the two can't drift.
_canonical_bentuk = canonical_bentuk
_BENTUK_SYNONYMS = _RANKING_SYNONYMS


def _compact_count(value: Any) -> str:
    """Render a count Indonesian-style: 1.240 → '1,2rb', 296975 → '297rb', 1.2M → '1,2jt'."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return ""
    if number < 1_000:
        return str(number)
    if number < 1_000_000:
        thousands = number / 1_000
        text = f"{thousands:.1f}".rstrip("0").rstrip(".") if thousands < 100 else f"{thousands:.0f}"
        return f"{text.replace('.', ',')}rb"
    millions = number / 1_000_000
    return f"{f'{millions:.1f}'.rstrip('0').rstrip('.').replace('.', ',')}jt"


def _caption_hook(caption: Any, limit: int = 140) -> str:
    """
    Extract the opening hook from a caption — the pattern actually worth adapting.

    Takes the text before the first hashtag / line break, collapses whitespace (so an
    exemplar can never inject prompt-structure markers), and truncates.
    """
    text = str(caption or "").strip()
    if not text:
        return ""
    text = text.split("#")[0]
    text = " ".join(text.split())
    return text[:limit].strip()


def _relative_age(age_days: Any) -> str:
    """'12 hari lalu' — more actionable to a model than an ISO timestamp."""
    try:
        days = int(round(float(age_days)))
    except (TypeError, ValueError):
        return ""
    if days <= 0:
        return "hari ini"
    if days < 30:
        return f"{days} hari lalu"
    if days < 365:
        return f"{days // 30} bulan lalu"
    return f"{days // 365} tahun lalu"


def _performer_brief(performers: List[Dict[str, Any]], label: str) -> str:
    """
    Condense ranked performers into prompt-friendly exemplar lines.

    Every enriched field is optional: rows come from the ranking task, but callers
    (and tests) may supply only the base columns, so each segment is dropped when
    its source is absent rather than rendering an empty placeholder.
    """
    lines = []
    for p in performers:
        eng = p.get("engagement")
        if eng is None:
            eng = (p.get("likes") or 0) + (p.get("comments") or 0)

        facts = [f"~{eng} interaksi"]
        views = p.get("views")
        if views:
            facts.append(f"{_compact_count(views)} views")
        rate = p.get("engagement_rate")
        if rate:
            facts.append(f"ER {rate * 100:.2f}%".replace(".", ","))
        age = _relative_age(p.get("age_days"))
        if age:
            facts.append(age)

        hook = _caption_hook(p.get("caption"))
        flow = " ".join(str(p.get("content_flow") or "").split())
        summary = " ".join(str(p.get("summary") or "").split())

        line = f"- [{label}] @{p.get('profile_key')} · {' · '.join(facts)}"
        if hook:
            line += f'\n  hook: "{hook}"'
        line += f"\n  alur: {flow[:220]} | ringkasan: {summary[:220]}"
        lines.append(line)
    return "\n".join(lines)


def _exemplar_block(own_perf: List[Dict[str, Any]], comp_perf: List[Dict[str, Any]]) -> str:
    """
    Render exemplars grouped by content type.

    Stratifying the selection is pointless if the model can't tell which exemplars are
    Posts — grouping is what makes a "4 Posts" instruction land against Post evidence.
    """
    labelled = [(p, "milik klien") for p in own_perf] + [(p, "kompetitor") for p in comp_perf]
    if not labelled:
        return "(belum ada sinyal konten yang di-harvest — bertumpu pada KB + parameter)"

    grouped: Dict[str, List[tuple]] = {}
    for performer, label in labelled:
        grouped.setdefault(performer.get("bucket") or "Lainnya", []).append((performer, label))

    blocks = []
    for bucket, entries in grouped.items():
        entries.sort(key=lambda e: -(e[0].get("score") or 0))
        body = "\n".join(_performer_brief([p], label) for p, label in entries)
        blocks.append(f"[{bucket}]\n{body}")
    return "\n".join(blocks)


def _signal_report(
    own_perf: List[Dict[str, Any]],
    comp_perf: List[Dict[str, Any]],
    own_stats: Dict[str, Any],
    comp_stats: Dict[str, Any],
    window_days: int,
) -> Dict[str, Any]:
    """
    Grade how well-grounded this run actually is (FR-011 "record reduced grounding").

    `signal_available` stays a bool for back-compat; this is the detail behind it.
    Coverage is judged purely on content-type buckets — folding own/competitor presence
    in would mean it never reads "full" for clients with no own account.
    """
    everything = own_perf + comp_perf
    by_bucket: Dict[str, int] = {}
    for performer in everything:
        bucket = performer.get("bucket") or "Lainnya"
        by_bucket[bucket] = by_bucket.get(bucket, 0) + 1

    quota: Dict[str, int] = {}
    for source in (comp_stats, own_stats):
        for bucket, want in (source.get("quota") or {}).items():
            quota[bucket] = max(quota.get(bucket, 0), want)
    gaps = sorted({b for b in quota if by_bucket.get(b, 0) == 0})

    ages = [p.get("age_days") for p in everything if p.get("age_days") is not None]
    if not everything:
        coverage = "none"
    elif gaps:
        coverage = "partial"
    else:
        coverage = "full"

    return {
        "exemplars": len(everything),
        "own": len(own_perf),
        "competitor": len(comp_perf),
        "accounts": len({p.get("profile_key") for p in everything}),
        "candidates_considered": (own_stats.get("candidates_considered") or 0)
        + (comp_stats.get("candidates_considered") or 0),
        "by_bucket": by_bucket,
        "quota": quota,
        "gaps": gaps,
        "coverage": coverage,
        "median_age_days": round(sorted(ages)[len(ages) // 2]) if ages else None,
        "window_days": window_days,
    }


def _allocation_summary(allocation: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Condense the per-slot theme allocation for the run outcome."""
    if not allocation:
        return None
    per_theme: Dict[str, int] = {}
    for slot in allocation:
        key = slot.get("theme") or "(tanpa tema)"
        per_theme[key] = per_theme.get(key, 0) + 1
    return {
        "per_theme": per_theme,
        "exploit": sum(1 for s in allocation if s.get("intent") == "exploit"),
        "explore": sum(1 for s in allocation if s.get("intent") == "explore"),
    }


def _allocation_block(allocation: List[Dict[str, Any]]) -> str:
    """
    Render the per-slot theme brief.

    Exploit slots must adapt a proven pattern with a *new* angle rather than restate
    it — repeated creative is what drives audience fatigue, so the distinction is
    spelled out per slot rather than left to the model.
    """
    if not allocation:
        return ""
    lines = []
    for index, slot in enumerate(allocation, start=1):
        theme = slot.get("theme") or "(bebas)"
        if slot.get("intent") == "exploit":
            lines.append(
                f"{index}. Tema '{theme}' — TERBUKTI perform. Adaptasi polanya dengan SUDUT "
                f"PANDANG BARU (angle/hook berbeda), jangan mengulang konten yang sama."
            )
        else:
            lines.append(
                f"{index}. Tema '{theme}' — belum teruji. Eksplorasi angle baru untuk menguji minat audiens."
            )
    return "\n".join(lines)


def _build_prompt(
    client_name: str,
    records: List[Dict[str, Any]],
    own_perf: List[Dict[str, Any]],
    comp_perf: List[Dict[str, Any]],
    params: Dict[str, Any],
    quantity: int,
    bucket: str,
    allocation: Optional[List[Dict[str, Any]]] = None,
    trends: Optional[List[Dict[str, Any]]] = None,
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

    # With no knowledge base, the harvested content *is* the grounding: the model must
    # infer voice, positioning and terminology from what the account and its competitors
    # actually publish, rather than inventing brand facts.
    if records:
        kb = "\n".join(f"- {r['subject']}: {r['information']}" for r in records)
        kb_section = f"Pengetahuan brand (knowledge base):\n{kb}\n\n"
    else:
        kb_section = (
            "Pengetahuan brand (knowledge base): TIDAK TERSEDIA untuk klien ini.\n"
            "Karena itu, jadikan konten yang di-harvest di bawah sebagai SATU-SATUNYA acuan brand: "
            "simpulkan gaya bahasa, positioning, layanan, dan istilah yang dipakai dari konten "
            "milik klien terlebih dahulu, lalu konten kompetitor sebagai pembanding pola. "
            "JANGAN mengarang fakta spesifik (nama dokter, harga, alamat, jam buka, klaim medis) — "
            "tulis sebagai [PLACEHOLDER: ...] agar diisi editor.\n\n"
        )
    exemplars = _exemplar_block(own_perf, comp_perf)

    pillars = ", ".join(params.get("content_pillars") or []) or "(bebas, sesuai brand)"

    # Composition is client configuration, not a model choice. Rather than asking for a
    # mixed batch and correcting it afterwards (which cost ideas when the model returned
    # the wrong split), each content type is generated in its own call.
    if bucket:
        mix_instruction = f"SEMUA ide harus berbentuk **{bucket}**. "
    else:
        # No configured mix (on-demand runs): the model picks the format per idea.
        mix_instruction = (
            f"Pilih `bentuk` yang paling cocok per ide, ditulis persis salah satu dari: "
            f"{' | '.join(DEFAULT_CONTENT_TYPES)}. "
        )

    rising = format_trends(trends or [])
    trend_block = f"Tema yang sedang naik (pertimbangkan, jangan dipaksakan):\n{rising}\n\n" if rising else ""
    plan_block = _allocation_block(allocation or [])
    plan_section = (
        f"Rencana tema per slot (ikuti urutan ini):\n{plan_block}\n\n" if plan_block else ""
    )

    user = (
        f"Klien: {client_name}\n"
        f"Platform: {params.get('platform') or '(umum)'}\n"
        f"Target audiens: {params.get('audience') or '(umum)'}\n"
        f"Tujuan kampanye: {params.get('goal') or '(brand awareness)'}\n"
        f"Tone: {params.get('tone') or '(sesuai brand)'}\n"
        f"Content pillars: {pillars}\n\n"
        f"{kb_section}"
        f"Konten yang terbukti perform (pelajari polanya, jangan salin):\n{exemplars}\n\n"
        f"{trend_block}"
        f"{plan_section}"
        f"Buatkan TEPAT {quantity} ide konten yang berbeda-beda. {mix_instruction}"
        f"Untuk setiap ide isi:\n"
        f"- topik: judul ide yang spesifik dan deskriptif (bukan satu kata).\n"
        f"- purpose_theme: 1-2 kalimat tujuan edukatif/persuasif konten ini.\n"
        f"- strategic_application: Awareness | Consideration | Conversion.\n"
        f"{_FORMAT_BRIEFS.get(bucket, _DEFAULT_FORMAT_BRIEF)}"
        f"- caption: caption siap posting dalam Bahasa Indonesia — buka dengan hook kuat "
        f"di kalimat pertama, badan yang jelas tapi ringkas, tutup dengan CTA, lalu hashtag "
        f"relevan di baris terakhir.\n"
        f"- adapted_pattern: pola menang yang diadaptasi (atau 'umum' bila tanpa sinyal).\n"
        f"- source_exemplar: handle/konten sumber (kosong bila tanpa sinyal).\n"
        f"- rationale: alasan ide ini cocok untuk klien + tujuannya.\n\n"
        f"Tandai fakta spesifik yang belum pasti (angka, nama dokter, harga, testimoni) sebagai "
        f"[PLACEHOLDER: ...] untuk diisi editor — jangan mengarang. JANGAN menyertakan tanggal."
    )
    return system, user


def _idea_cells(idea: Dict[str, Any], date_str: str, row_number: int) -> Dict[str, str]:
    """
    Map one generated idea to a {column_name: cell} dict in the v5 plan layout.

    Columns outside `GENERATED_COLUMNS` are intentionally absent here and render blank —
    scheduling (`Waktu`), production (`Kebutuhan Personil`, `Asset`), and workflow
    (`Approval`, `TicketID`, `Key`) fields belong to the humans downstream.
    """
    bentuk = idea.get("bentuk", "")
    return {
        "No.": str(row_number),
        "Tanggal": date_str,
        "Bentuk": bentuk,
        "Topik": idea.get("topik", ""),
        "Creator": DEFAULT_CREATOR,
        # `Format` mirrors `Bentuk` in the v5 plans rather than carrying a separate
        # vocabulary, so it is derived instead of generated.
        "Format": bentuk,
        "Purpose/Theme": idea.get("purpose_theme", ""),
        "Strategic Application": idea.get("strategic_application", ""),
        "Shoot Guide": idea.get("shoot_guide", ""),
        "Reference": idea.get("reference", ""),
        "Caption": idea.get("caption", ""),
    }


# How many extra calls a bucket may make to fill its quota before giving up. Models
# routinely return one or two fewer than asked; a top-up costs far less than shipping
# a client fewer pieces than they are contracted for.
MAX_TOPUP_ATTEMPTS = 3
# Output budget per idea. A caption plus a slide-by-slide carousel design or a
# scene-by-scene shoot plan runs long: 900 still truncated a 7-Post batch in practice
# (recovered by the top-up, at the cost of an extra call). This is an upper bound, not
# a cost floor — only generated tokens are billed — so it is set generously.
TOKENS_PER_IDEA = 1500


async def _generate_bucket(
    *,
    bucket: str,
    want: int,
    client: str,
    client_name: str,
    records: List[Dict[str, Any]],
    own_perf: List[Dict[str, Any]],
    comp_perf: List[Dict[str, Any]],
    params: Dict[str, Any],
    allocation: Optional[List[Dict[str, Any]]],
    trends: Optional[List[Dict[str, Any]]],
    run_logger,
) -> tuple[List[Dict[str, Any]], int]:
    """
    Generate exactly `want` ideas of one content type, retrying for any shortfall.

    Generating per type (rather than asking for a mixed batch and correcting it) means
    the composition can't come out wrong, and a deficit is re-requested instead of
    silently short-shipping the client.

    Returns:
        (ideas, total items the model returned across attempts)
    """
    produced: List[Dict[str, Any]] = []
    returned_total = 0
    seen_topics: set = set()

    for attempt in range(MAX_TOPUP_ATTEMPTS):
        missing = want - len(produced)
        if missing <= 0:
            break
        if attempt:
            run_logger.info(
                f"{client}: topping up '{bucket}' — {len(produced)}/{want} so far, "
                f"requesting {missing} more (attempt {attempt + 1})"
            )
        system, user = _build_prompt(
            client_name, records, own_perf, comp_perf, params, missing, bucket,
            allocation=allocation, trends=trends,
        )
        if produced:
            # Avoid re-generating what we already have on a top-up.
            existing = "; ".join(str(p.get("topik", "")) for p in produced)
            user += f"\n\nHINDARI mengulang topik yang sudah ada: {existing}"
        # `bentuk` is only requested when there is no configured type for this call.
        keys = IDEA_KEYS if bucket else IDEA_KEYS + ["bentuk"]
        try:
            result = await openrouter_chat(
                user=user, system=system,
                response_format=array_schema("content_ideas", keys),
                max_tokens=min(TOKENS_PER_IDEA * missing + 800, 16000),
                call_site=f"songbird.generate[{bucket or 'mixed'}]",
                client=client,
            )
        except Exception as e:
            run_logger.warning(f"{client}: '{bucket}' generation attempt {attempt + 1} failed: {e}")
            continue

        items = result.get("items", []) if isinstance(result, dict) else []
        returned_total += len(items)
        for item in items:
            if not isinstance(item, dict) or not str(item.get("topik", "")).strip():
                run_logger.warning(f"{client}: dropped a malformed '{bucket}' idea (missing topik)")
                continue
            topic_key = " ".join(str(item["topik"]).lower().split())
            if topic_key in seen_topics:
                run_logger.warning(f"{client}: dropped a duplicate '{bucket}' idea: {item['topik']!r}")
                continue
            seen_topics.add(topic_key)
            if bucket:
                # The engine owns `bentuk` — this call was for one type by construction.
                item["bentuk"] = bucket
            else:
                item["bentuk"] = (
                    canonical_bentuk(str(item.get("bentuk", "")), DEFAULT_CONTENT_TYPES)
                    or DEFAULT_CONTENT_TYPES[0]
                )
            produced.append(item)
            if len(produced) >= want:
                break

    if len(produced) < want:
        run_logger.warning(
            f"{client}: '{bucket}' still short after {MAX_TOPUP_ATTEMPTS} attempts "
            f"({len(produced)}/{want})"
        )
    return produced[:want], returned_total


async def run_generation(
    client: str,
    quantity: Optional[int] = None,
    *,
    content_mix: Optional[Dict[str, int]] = None,
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
    signal_half_life_days: float = RECENCY_HALF_LIFE_DAYS,
    own_handles: Optional[List[str]] = None,
    competitor_handles: Optional[List[str]] = None,
    live_spreadsheet_id: Optional[str] = None,
    live_tab: Optional[str] = None,
    exemplar_limit: Optional[int] = None,
    allocation_seed: Optional[int] = None,
    credentials_block_name: str = "google-creds",
) -> Dict[str, Any]:
    """
    Generate a batch of targeted content ideas and deliver them (draft or live).

    Args:
        client: Client name (grounds KB lookup, handle resolution, config quantity).
        quantity: Total number of ideas, with the content type left to the model.
            Overrides content_mix; if both are None and resolve_quantity_from_config
            is set, the per-type mix is read from the Clients worksheet (FR-003b).
        content_mix: Explicit {content_type: amount}; the total is their sum and the
            composition is enforced on the generated ideas.
        distribute_dates: Assign month-distributed publish dates (monthly plan only).
        resolve_quantity_from_config: Resolve the per-type content mix from the
            Clients worksheet (Post / Story / Short Video columns).
        month: Target month "Month YYYY" (required when distribute_dates).
        target: "draft" (default) or "live" (monthly only) — FR-015.
        platform/audience/goal/tone/content_pillars: marketing params (FR-003).
        signal_window_days: Rolling recency window for top performers (FR-009a).
        signal_half_life_days: Half-life of the recency tilt applied to exemplar scores.
        own_handles/competitor_handles: per-run handle overrides, merged with
            CLIENT_SOCIAL and the Clients worksheet's own profile URLs.
        live_spreadsheet_id/live_tab: live content-plan worksheet target.
        exemplar_limit: TOTAL exemplars across own + competitor (not per group).
            None ⇒ scale with the plan size, capped at MAX_EXEMPLARS.
        allocation_seed: Seed for theme sampling; set it to make a plan reproducible.
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
        "content_mix_requested": dict(content_mix) if content_mix else None,
        "content_mix_delivered": None,
        "ideas_returned_by_model": 0,
        "ideas_produced": 0,
        "ideas_failed": 0,
        "signal_available": False,
        "signal": None,
        "grounding": None,
        "trends": [],
        "themes": [],
        "allocation": None,
        "target": "draft",
        "deliverable_id": None,
        "hit_disclaimer": HIT_DISCLAIMER,
    }
    error: Optional[str] = None
    end_time = start_time

    try:
        # 1. Resolve how much to make (fail fast per FR-003b when unconfigured).
        #    An explicit `quantity` overrides the mix; otherwise the per-type
        #    amounts from the Clients worksheet define both size and composition.
        if quantity is None and content_mix is None and resolve_quantity_from_config:
            content_mix = await songbird_config_content_mix(
                client, credentials_block_name=credentials_block_name
            )
        if quantity is None and content_mix:
            content_mix = {k: int(v) for k, v in content_mix.items() if int(v or 0) > 0}
            quantity = sum(content_mix.values())
        else:
            content_mix = None  # explicit quantity wins; model picks the bentuk
        if not quantity or quantity <= 0:
            raise ValueError("No positive content quantity resolved for this run")
        summary["ideas_requested"] = quantity
        summary["content_mix_requested"] = dict(content_mix) if content_mix else None
        if content_mix:
            run_logger.info(
                f"{client}: content mix "
                f"{', '.join(f'{k}={v}' for k, v in content_mix.items())} (total {quantity})"
            )

        # 2. Dates (monthly only) — computed here, never by the model (FR-004).
        if distribute_dates:
            if not month:
                raise ValueError("month is required when distribute_dates is set")
            year, month_num = _parse_month(month)
            dates = _distribute_dates(year, month_num, quantity)
        else:
            dates = [""] * quantity

        # 3. Gather signals.
        #    Exemplar budget scales with the plan size (~2 per idea) and is capped so
        #    the prompt's context cost stays bounded.
        budget = exemplar_limit if exemplar_limit else max(8, min(2 * quantity, MAX_EXEMPLARS))
        sheet_own = await songbird_config_own_handles(
            client, credentials_block_name=credentials_block_name
        )
        own, competitors = _resolve_handles(client, own_handles, competitor_handles, sheet_own)
        context = await songbird_client_context(client)
        records = context.get("records", [])
        client_name = context.get("client_name", client)

        # With no knowledge base, harvested content is the only grounding there is — so
        # widen the exemplar budget to give the model more of it to work from.
        if not records:
            budget = min(math.ceil(budget * NO_KB_EXEMPLAR_BOOST), MAX_EXEMPLARS)

        # Own content gets a reserved share; whatever it doesn't use goes to competitors,
        # so a client with no harvested own account still gets a full exemplar set.
        own_stats: Dict[str, Any] = {}
        comp_stats: Dict[str, Any] = {}
        own_budget = math.ceil(budget * OWN_EXEMPLAR_SHARE) if own else 0
        own_perf = await songbird_top_performers(
            own, limit=own_budget, window_days=signal_window_days,
            content_mix=content_mix, recency_half_life_days=signal_half_life_days,
            stats=own_stats,
        ) if own_budget else []
        comp_budget = budget - len(own_perf)
        comp_perf = await songbird_top_performers(
            competitors, limit=comp_budget, window_days=signal_window_days,
            content_mix=content_mix, recency_half_life_days=signal_half_life_days,
            stats=comp_stats,
        ) if competitors and comp_budget > 0 else []

        summary["signal_available"] = bool(own_perf or comp_perf)
        summary["signal"] = _signal_report(
            own_perf, comp_perf, own_stats, comp_stats, signal_window_days
        )
        # What this plan is actually grounded in — recorded so a reviewer can judge how
        # much to trust it, and warned about at the level the situation deserves (FR-011).
        has_kb, has_signal = bool(records), bool(own_perf or comp_perf)
        summary["grounding"] = (
            "knowledge_base+signal" if has_kb and has_signal
            else "signal_only" if has_signal
            else "knowledge_base_only" if has_kb
            else "params_only"
        )
        if not has_kb and has_signal:
            run_logger.info(
                f"{client}: no knowledge base — generating purely from harvested content "
                f"({len(own_perf)} own + {len(comp_perf)} competitor exemplars)"
            )
        elif not has_signal and has_kb:
            run_logger.warning(f"{client}: no harvested signal — generating from KB + params only (FR-011)")
        elif not has_signal and not has_kb:
            run_logger.warning(
                f"{client}: neither knowledge base nor harvested signal — generating from "
                f"marketing params alone; treat this draft as a starting point (FR-011)"
            )
        for gap in (summary["signal"]["gaps"] if has_signal else []):
            run_logger.warning(
                f"{client}: no '{gap}' exemplars available — those ideas lean on the other types"
            )
        if not own:
            run_logger.warning(
                f"{client}: no own-account handle resolved — learning only from competitors (FR-008)"
            )
        if not records:
            run_logger.warning(f"{client}: no knowledge-base records found — grounding reduced")

        # 4. Strategy: what is rising, and how to split the month between doubling
        #    down on proven themes and exploring new ones. Both are best-effort —
        #    generation must never fail because a strategy layer did (FR-011).
        exemplars = own_perf + comp_perf
        # Trends run over the whole scored population, not the handful of picks —
        # a burst needs a baseline to be measured against.
        trend_corpus = (own_stats.get("scored") or []) + (comp_stats.get("scored") or [])
        trends: List[Dict[str, Any]] = []
        allocation: List[Dict[str, Any]] = []
        themes: List[Dict[str, Any]] = []
        if exemplars:
            try:
                trends = detect_trends(trend_corpus or exemplars, limit=8)
            except Exception as e:
                run_logger.warning(f"{client}: trend detection failed (non-fatal): {e}")
            try:
                themes = await songbird_theme_induction(exemplars, client=client)
            except Exception as e:
                run_logger.warning(f"{client}: theme induction failed (non-fatal): {e}")
            if not themes:
                themes = themes_from_pillars(content_pillars or [])
            if themes:
                try:
                    allocation = allocate_slots(themes, quantity, seed=allocation_seed)
                except Exception as e:
                    run_logger.warning(f"{client}: theme allocation failed (non-fatal): {e}")
        summary["trends"] = [t["token"] for t in trends]
        summary["themes"] = [{"theme": t["theme"], "evidence": len(t["scores"])} for t in themes]
        summary["allocation"] = _allocation_summary(allocation)

        # 5. Generate.
        params = {"platform": platform, "audience": audience, "goal": goal, "tone": tone, "content_pillars": content_pillars}
        # One call per content type, each topped up until its quota is met — the client
        # is contracted for a specific amount of each, so a short bucket is a failure to
        # deliver, not an acceptable outcome.
        plan = content_mix or {"": quantity}
        by_bucket: Dict[str, List[Dict[str, Any]]] = {}
        returned_total = 0
        for bucket, want in plan.items():
            produced, returned = await _generate_bucket(
                bucket=bucket, want=want, client=client, client_name=client_name,
                records=records, own_perf=own_perf, comp_perf=comp_perf, params=params,
                allocation=allocation, trends=trends, run_logger=run_logger,
            )
            by_bucket[bucket] = produced
            returned_total += returned

        # Interleave so publish dates alternate content types across the month.
        ideas: List[Dict[str, Any]] = []
        for index in range(max((len(v) for v in by_bucket.values()), default=0)):
            for bucket in plan:
                if index < len(by_bucket[bucket]):
                    ideas.append(by_bucket[bucket][index])

        summary["ideas_returned_by_model"] = returned_total
        if content_mix:
            summary["content_mix_delivered"] = {b: len(v) for b, v in by_bucket.items()}
        # Single failure tally: every requested idea not delivered counts once — SC-007.
        summary["ideas_failed"] = max(quantity - len(ideas), 0)
        if summary["ideas_failed"]:
            run_logger.warning(f"{client}: delivered {len(ideas)}/{quantity} usable ideas")

        # 7. Build rows.
        rows_cells = [
            _idea_cells(idea, dates[i] if i < len(dates) else "", i + 1)
            for i, idea in enumerate(ideas)
        ]
        if not rows_cells:
            raise ValueError("No usable content ideas were generated")

        # 8. Deliver.
        effective_target = target if distribute_dates else "draft"
        summary["target"] = effective_target
        if effective_target == "live":
            deliverable_id = await _deliver_live(
                rows_cells, live_spreadsheet_id, live_tab, credentials_block_name, run_logger
            )
        else:
            draft_folder_id = await songbird_config_draft_folder(
                client, credentials_block_name=credentials_block_name
            )
            # Title from the *requested* name: the Clients worksheet is authoritative
            # for client identity, the knowledge base only supplies grounding.
            deliverable_id = await _deliver_draft(
                client, month, distribute_dates, rows_cells, credentials_block_name,
                run_logger, draft_folder_id=draft_folder_id,
            )
        summary["deliverable_id"] = deliverable_id

        # The draft sheet mirrors the v5 plan layout, which has no rationale columns —
        # so the "why" behind each idea is preserved here, in the run outcome (FR-005).
        data = [
            {
                "tanggal": cells["Tanggal"], "topik": cells["Topik"], "bentuk": cells["Bentuk"],
                "adapted_pattern": idea.get("adapted_pattern", ""),
                "source_exemplar": idea.get("source_exemplar", ""),
                "rationale": idea.get("rationale", ""),
                "hit_note": HIT_DISCLAIMER,
            }
            for cells, idea in zip(rows_cells, ideas)
        ]
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
    draft_folder_id: Optional[str] = None,
) -> str:
    """
    Create a draft Sheet (content-plan + rationale columns) and append rows (FR-016a).

    Delivered into the client's own Content Plan folder when the Clients worksheet
    names one, so reviewers find drafts beside the real plans; SONGBIRD_DRIVE_PARENT_ID
    is the shared fallback for clients without that column filled in.
    """
    if draft_folder_id:
        parent_id = draft_folder_id
    else:
        parent_id = (os.environ.get("SONGBIRD_DRIVE_PARENT_ID") or "").strip()
        if not parent_id:
            raise ValueError(
                f"No draft folder for '{client_name}': the Clients worksheet has no "
                f"Content Plan Folder ID for this client and SONGBIRD_DRIVE_PARENT_ID is unset"
            )
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
    # Only the columns songbird actually fills matter here — the workflow columns are
    # expected to be absent or empty, so flagging them would be noise.
    missing = [c for c in CONTENT_PLAN_COLUMNS if c in GENERATED_COLUMNS and c not in header]
    if missing:
        run_logger.warning(f"Live worksheet missing content-plan columns {missing}; those cells left blank")
    # Align by column NAME; rationale columns are excluded (not in content-plan header set).
    rows = [[cells.get(col, "") if col in CONTENT_PLAN_COLUMNS else "" for col in header] for cells in rows_cells]
    await sheets_rows_append(spreadsheet_id, rows, sheet_name=tab, credentials_block_name=credentials_block_name)
    run_logger.info(f"Live delivered: {len(rows)} rows appended to '{tab}' ({spreadsheet_id})")
    return spreadsheet_id
