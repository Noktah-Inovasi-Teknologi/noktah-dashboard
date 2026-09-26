"""
Working days, for the Incentive Framework's monthly steps (v2.1 §7.1, spec 009 R10):
sanctions are worked out on working day 2 and issued once working day 3 has ended.

A working day is Monday to Friday, minus the Indonesian national holidays listed by hand in
config/hub/holidays.yaml. Times are WIB (UTC+7, no daylight saving), so a fixed offset is
exact and needs no tz database in the slim image.
"""
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import FrozenSet, Iterable, Optional

import yaml

WIB = timezone(timedelta(hours=7), "WIB")


@lru_cache
def _file_holidays(path: str) -> FrozenSet[date]:
    p = Path(path)
    if not p.exists():
        return frozenset()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out = set()
    for d in data.get("holidays") or []:
        out.add(d if isinstance(d, date) else date.fromisoformat(str(d)))
    return frozenset(out)


def holidays() -> FrozenSet[date]:
    from .settings import get_settings
    return _file_holidays(str(Path(get_settings().card_definition_dir) / "holidays.yaml"))


def is_working_day(d: date, off: Optional[Iterable[date]] = None) -> bool:
    off = holidays() if off is None else frozenset(off)
    return d.weekday() < 5 and d not in off


def working_day(year: int, month: int, n: int, off: Optional[Iterable[date]] = None) -> date:
    """The n-th working day (1-based) of a month."""
    off = holidays() if off is None else frozenset(off)
    d, seen = date(year, month, 1), 0
    while True:
        if is_working_day(d, off):
            seen += 1
            if seen == n:
                return d
        d += timedelta(days=1)


def next_working_day(d: date, off: Optional[Iterable[date]] = None) -> date:
    off = holidays() if off is None else frozenset(off)
    d += timedelta(days=1)
    while not is_working_day(d, off):
        d += timedelta(days=1)
    return d


def end_of(d: date) -> datetime:
    """The end of a WIB calendar day, as an aware datetime (the start of the next day)."""
    return datetime.combine(d + timedelta(days=1), time(0, 0), tzinfo=WIB)


def now_wib(now: Optional[datetime] = None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(WIB)


def previous_month(d: date) -> date:
    first = d.replace(day=1)
    return (first - timedelta(days=1)).replace(day=1)
