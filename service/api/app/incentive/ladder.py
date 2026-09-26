"""
The sanction ladder of Incentive Framework v2.1 §3.2 (spec 009 R10, ADR-0001). Pure: no I/O.

    10–20 points                                         teguran lisan
    3 teguran lisan within 90 days with the same code    SP1
    30–50 points, no active SP                           SP1
    30–50 points, SP1 active                             SP2
    30–50 points, SP2 active                             SP3
    60+ points, or 30+ two months running                one level above the active SP (none → SP1) + 30-day PIP
    violating again while SP3 is active, or a failed PIP proses PHK (only ever flagged)
The heaviest row that applies wins. Teguran and SP are valid 90 days from issue.

Direct sanctions (§4.3, and H1 retaliation) skip the ladder: 4.3.1 → SP1, 4.3.2 and H1 →
Peringatan Pertama dan Terakhir (equal to SP3), 4.3.3 → proses PHK.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, List, Optional

VALID_DAYS = 90
RANK = {"teguran_lisan": 1, "sp1": 2, "sp2": 3, "sp3": 4, "peringatan_terakhir": 4, "phk_flag": 5}
NEXT = {None: "sp1", "sp1": "sp2", "sp2": "sp3", "sp3": "phk_flag", "peringatan_terakhir": "phk_flag"}
LETTER_LEVELS = ("sp1", "sp2", "sp3", "peringatan_terakhir")
LABELS = {"teguran_lisan": "Teguran lisan", "sp1": "SP1", "sp2": "SP2", "sp3": "SP3",
          "peringatan_terakhir": "Peringatan Pertama dan Terakhir", "phk_flag": "Proses PHK"}


@dataclass(frozen=True)
class Issued:
    """A teguran lisan or SP already issued (recorded by a manager, or by the Hub)."""
    level: str
    issued_on: date
    category_code: Optional[str] = None

    def active_on(self, d: date) -> bool:
        return self.issued_on <= d < self.issued_on + timedelta(days=VALID_DAYS)


@dataclass(frozen=True)
class Verdict:
    level: str
    with_pip: bool
    rule: str


def active_sp(history: Iterable[Issued], on: date) -> Optional[str]:
    """The highest SP still valid on `on` (a Peringatan Pertama dan Terakhir counts as SP3)."""
    best = None
    for h in history:
        if h.level != "teguran_lisan" and h.level != "phk_flag" and h.active_on(on):
            if best is None or RANK[h.level] > RANK[best]:
                best = h.level
    return "sp3" if best == "peringatan_terakhir" else best


def decide(points: int, previous_points: int, category_code: Optional[str], history: List[Issued],
           on: date) -> Optional[Verdict]:
    """The sanction for one person's month. `points` and `previous_points` are the magnitudes
    of the month's and the previous month's Violation points (e.g. 30 for −30)."""
    points, previous_points = abs(points), abs(previous_points)
    if points <= 0:
        return None
    active = active_sp(history, on)
    options: List[Verdict] = []
    if active == "sp3":
        options.append(Verdict("phk_flag", False, "Kembali melanggar saat SP3 aktif"))
    if 10 <= points <= 20:
        options.append(Verdict("teguran_lisan", False, "10–20 poin"))
        same = [h for h in history if h.level == "teguran_lisan" and h.category_code and
                h.category_code == category_code and h.active_on(on)]
        if category_code and len(same) + 1 >= 3:
            options.append(Verdict("sp1", False, f"3 teguran lisan dalam 90 hari dengan kode {category_code}"))
    if 30 <= points <= 50:
        options.append(Verdict(NEXT[active], False, f"30–50 poin, {'SP aktif: ' + LABELS[active] if active else 'tidak ada SP aktif'}"))
    if points >= 60 or (points >= 30 and previous_points >= 30):
        rule = "60 poin atau lebih" if points >= 60 else "30 poin atau lebih dua bulan berturut-turut"
        level = NEXT[active]
        options.append(Verdict(level, level != "phk_flag", rule + " (+ PIP 30 hari)" if level != "phk_flag" else rule))
    if not options:
        return None
    return max(options, key=lambda v: (RANK[v.level], v.with_pip))


def direct(item: Optional[str], category_code: Optional[str]) -> Optional[Verdict]:
    """A direct sanction from its §4.3 item (e.g. "4.3.2-1"), or H1 retaliation."""
    if category_code == "H1":
        return Verdict("peringatan_terakhir", False, "H1: pembalasan terhadap pelapor (Bagian 4.1.6)")
    if not item:
        return None
    section = item.split("-")[0]
    if section == "4.3.1":
        return Verdict("sp1", False, f"Pelanggaran {item}: langsung SP1")
    if section == "4.3.2":
        return Verdict("peringatan_terakhir", False, f"Pelanggaran {item}: langsung peringatan pertama dan terakhir")
    if section == "4.3.3":
        return Verdict("phk_flag", False, f"Pelanggaran {item}: PHK tanpa SP")
    return None
