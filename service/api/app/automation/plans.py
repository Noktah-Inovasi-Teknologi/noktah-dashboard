"""
Content Plan rules (spec 009 US1, research R4/R5). Pure: no I/O.

A plan is read as rows: [{"row_number": 2, "cells": {"Tanggal": "04/10/2026", ...}}].

Fingerprints cover only the columns an issue is built from. Approval, Keterangan, Key,
TicketID and No. are left out, so writing the Key back never looks like a change, and
neither does a planner ticking the plan's own Approval column.
"""
import hashlib
import json
import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

FINGERPRINT_COLUMNS = (
    "Tanggal", "Waktu", "Bentuk", "Topik", "Creator", "Format", "Purpose/Theme", "Strategic Application",
    "Kebutuhan Personil", "Known Facts", "Shoot Guide", "Visualisasi Konten", "Asset", "Caption",
    "Link Referensi",
)
REQUIRED = ("Tanggal", "Topik", "Bentuk")
KEY_COLUMN = "Key"
CONTENT_TYPES = ("Post", "Story", "Short Video")
KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")

MONTHS_ID = ("Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September",
             "Oktober", "November", "Desember")


def norm(value: Any) -> str:
    return " ".join(str(value if value is not None else "").split())


def row_fingerprint(cells: Dict[str, Any]) -> str:
    payload = json.dumps([norm(cells.get(c)) for c in FINGERPRINT_COLUMNS], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def is_blank(cells: Dict[str, Any]) -> bool:
    return not any(norm(cells.get(c)) for c in FINGERPRINT_COLUMNS)


def content_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [r for r in rows if not is_blank(r.get("cells") or {})]


def plan_fingerprint(rows: List[Dict[str, Any]]) -> str:
    parts = [f"{r['row_number']}:{row_fingerprint(r['cells'])}" for r in content_rows(rows)]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def key_of(cells: Dict[str, Any]) -> Optional[str]:
    m = KEY_RE.search(norm(cells.get(KEY_COLUMN)))
    return m.group(1) if m else None


def month_label(month: date) -> str:
    """'Oktober 2026', as in "Content Plan - {Client} - {month}"."""
    return f"{MONTHS_ID[month.month - 1]} {month.year}"


def parse_date(value: Any) -> Optional[date]:
    s = norm(value)
    if not s:
        return None
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", s)
        if not m:
            return None
        d, mo, y = map(int, m.groups())
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def bentuk(value: Any) -> Optional[str]:
    """The canonical content type (the Registry quota's columns)."""
    s = norm(value).lower()
    if not s:
        return None
    if "story" in s:
        return "Story"
    if "short" in s or "video" in s or "reel" in s:
        return "Short Video"
    if "post" in s or "carousel" in s or "feed" in s or "image" in s:
        return "Post"
    return norm(value)


def changes(old: Dict[str, Any], new: Dict[str, Any]) -> List[Dict[str, str]]:
    return [{"column": c, "old": norm(old.get(c)), "new": norm(new.get(c))}
            for c in FINGERPRINT_COLUMNS if norm(old.get(c)) != norm(new.get(c))]


def assess(rows: List[Dict[str, Any]], month: date, quota: Dict[str, Optional[int]], *,
           has_field_associate: bool, has_content_editor: bool, fa_has_jira: bool, ce_has_jira: bool,
           has_component: bool) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """(blocking, warnings, quota counts) for the Greenlight screen (G-30)."""
    blocking: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    if not has_field_associate:
        blocking.append({"code": "no_field_associate", "message": "Tim klien belum punya Field Associate."})
    elif not fa_has_jira:
        blocking.append({"code": "no_jira_account",
                         "message": "Field Associate di tim klien belum punya Jira account ID. Isi di halaman Orang."})
    if not has_content_editor:
        blocking.append({"code": "no_content_editor", "message": "Tim klien belum punya Content Editor."})
    elif not ce_has_jira:
        blocking.append({"code": "no_jira_account",
                         "message": "Content Editor di tim klien belum punya Jira account ID. Isi di halaman Orang."})
    if not has_component:
        blocking.append({"code": "no_component", "message": "Klien belum punya komponen Jira di Registry."})

    counts = {t: 0 for t in CONTENT_TYPES}
    for r in content_rows(rows):
        cells = r["cells"]
        missing = [c for c in REQUIRED if not norm(cells.get(c))]
        if missing:
            blocking.append({"code": "row_missing_fields", "row_number": r["row_number"],
                             "message": f"Baris {r['row_number']} belum punya {', '.join(missing)}."})
        d = parse_date(cells.get("Tanggal"))
        if norm(cells.get("Tanggal")) and (d is None or (d.year, d.month) != (month.year, month.month)):
            warnings.append({"code": "date_outside_month", "row_number": r["row_number"],
                             "message": f"Baris {r['row_number']}: tanggal {norm(cells.get('Tanggal'))} di luar bulan ini."})
        t = bentuk(cells.get("Bentuk"))
        if t in counts:
            counts[t] += 1
    summary = {}
    for t in CONTENT_TYPES:
        q = quota.get(t)
        summary[t] = {"planned": counts[t], "quota": q}
        if q is not None and counts[t] != q:
            warnings.append({"code": "quota_mismatch",
                             "message": f"{t}: {counts[t]} di Content Plan, kuota {q}."})
    return blocking, warnings, summary
