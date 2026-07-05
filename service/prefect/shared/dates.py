"""
Date and time helpers shared across workflows.

Centralizes UTC timestamp generation (fixing the previous
``datetime.now().isoformat() + "Z"`` bug that labeled naive *local* time as UTC)
and the Indonesian/English month mappings used for content-plan filenames.
"""
from datetime import datetime, timezone
from typing import Dict, Optional

# Month-number -> localized month name
INDONESIAN_MONTHS: Dict[int, str] = {
    1: "Januari", 2: "Februari", 3: "Maret", 4: "April",
    5: "Mei", 6: "Juni", 7: "Juli", 8: "Agustus",
    9: "September", 10: "Oktober", 11: "November", 12: "Desember",
}

ENGLISH_MONTHS: Dict[int, str] = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}

# Reverse lookups: month name -> number
INDONESIAN_MONTH_REVERSE: Dict[str, int] = {v: k for k, v in INDONESIAN_MONTHS.items()}
ENGLISH_MONTH_REVERSE: Dict[str, int] = {v: k for k, v in ENGLISH_MONTHS.items()}


def utc_now_iso() -> str:
    """
    Return the current time as a timezone-aware UTC ISO-8601 string.

    Produces e.g. ``2026-07-05T14:03:21.123456+00:00`` -- a *correct* UTC
    timestamp, unlike ``datetime.now().isoformat() + "Z"`` which mislabels
    local time as UTC.
    """
    return datetime.now(timezone.utc).isoformat()


def month_number(month_name: str) -> Optional[int]:
    """
    Resolve an Indonesian or English month name to its 1-12 number.

    Args:
        month_name: e.g. "Juli", "July".

    Returns:
        Month number, or None if the name is not recognized.
    """
    name = month_name.strip()
    if name in INDONESIAN_MONTH_REVERSE:
        return INDONESIAN_MONTH_REVERSE[name]
    if name in ENGLISH_MONTH_REVERSE:
        return ENGLISH_MONTH_REVERSE[name]
    return None


def format_month_year(month: int, year: int, language: str = "indonesian") -> str:
    """
    Format a month/year as ``"<MonthName> <YYYY>"`` in the given language.

    Args:
        month: Month number (1-12).
        year: Four-digit year.
        language: "indonesian" (default) or "english".

    Returns:
        e.g. "Juli 2026".
    """
    months = ENGLISH_MONTHS if language == "english" else INDONESIAN_MONTHS
    return f"{months[month]} {year}"
