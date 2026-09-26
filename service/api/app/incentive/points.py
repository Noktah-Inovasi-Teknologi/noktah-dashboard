"""
Points of one judged Event, under Incentive Framework v2.1 (spec 009 R9, G-26…G-28, G-34,
G-43, G-45, G-47). Pure: no I/O.

Violation / Self-report:
    Violation Judgment (P1) other than "Kelalaian" → 0 points ("catatan sistem" and the rest).
    Otherwise points = −10 × P2 × P3
        P2  Event Observer: Internal (Staff) 1, External (Client) 3. The field allows several
            values; with both ticked the client knew, so it is 3 (decided 2026-09-26, G-51).
        P3  ×0 when Reporter's Own Mistake/Error = Yes and Problem/Error Solved = Yes (self-report)
            ×2 when Known by the Person = Yes
            ×1 otherwise
Excellence: each ticked Excellence Category, +1 (X1, X2, X3, X8) or +3 (X4…X7); several ticked
categories add up (G-51).
The category of a Violation is its Violation Category: Events carry no Defect Category (G-50);
a content defect's origin station comes from the Return Screen on the Content ticket.

Signs (G-45): Violations negative, Excellences positive, everywhere.

The Hub never guesses (G-26): an Event missing a field its computation needs, or holding
several values in a single-choice field, is `belum_lengkap` and counts nothing.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

# Points and sanctions count from here; earlier Events are shown for reference only (G-47).
COUNTING_FROM = date(2026, 9, 27)
ADAPTATION_DAYS = 30  # v2.1 §8

F_TYPE = "Event Type"
F_JUDGMENT = "Violation Judgment"
F_OBSERVER = "Event Observer"
F_OWN = "Reporter's Own Mistake/Error"
F_SOLVED = "Problem/Error Solved"
F_KNOWN = "Known by the Person"
F_DEFECT = "Defect Category"
F_VIOLATION = "Violation Category"
F_EXCELLENCE = "Excellence Category"
F_DIRECT = "Direct Sanction Violation"
F_PERSON = "Person"
F_ASSIGNEE = "Assignee"

EXCELLENCE_POINTS = {"X1": 1, "X2": 1, "X3": 1, "X4": 3, "X5": 3, "X6": 3, "X7": 3, "X8": 1}
OBSERVER_WEIGHT = {"internal": 1, "external": 3}


@dataclass
class Outcome:
    """What the Hub makes of one Event."""
    state: str                 # counted | zero | belum_lengkap | reference | adaptation | on_hold | not_judged
    points: int = 0            # signed; only `counted` and `zero` add to a month
    kind: Optional[str] = None  # violation | excellence
    category: Optional[str] = None  # e.g. "C2", "W7", "X4", "H2"
    letter: Optional[str] = None     # e.g. "C", "W", "H"
    direct_item: Optional[str] = None  # e.g. "4.3.2-1", for H2 with Direct Sanction Violation
    missing: List[str] = field(default_factory=list)
    reason: Optional[str] = None
    formula: Optional[str] = None     # "Klien × ditemukan orang lain"

    @property
    def counts(self) -> bool:
        return self.state in ("counted", "zero")


def _values(v: Any) -> List[str]:
    """A field as a list of chosen values (single choice, multi choice or empty)."""
    if v is None or v == "" or v == []:
        return []
    if isinstance(v, list):
        return [str(x) for x in v if x not in (None, "")]
    if isinstance(v, dict):
        return [str(v.get("child") or v.get("parent") or v.get("value") or "")] if (v.get("child") or v.get("parent") or v.get("value")) else []
    return [str(v)]


def _one(fields: Dict[str, Any], name: str, missing: List[str]) -> Optional[str]:
    vals = _values(fields.get(name))
    if len(vals) != 1:
        missing.append(name)
        return None
    return vals[0].strip()


def _yes(v: Optional[str]) -> bool:
    return (v or "").strip().lower() in ("yes", "ya")


def category(v: Any) -> (Optional[str], Optional[str]):
    """(code, letter) of a cascading category: {'parent': 'C - Design…', 'child': 'C2 - Tidak…'}."""
    if not v:
        return None, None
    if isinstance(v, dict):
        child, parent = (v.get("child") or "").strip(), (v.get("parent") or "").strip()
    else:
        child, parent = str(v).strip(), ""
    code = child.split(" ")[0].strip() if child else None
    letter = (parent[:1] or (code or "")[:1]).upper() or None
    if code and not code[:1].isalpha():
        code = None
    return code or letter, letter


def person_account(fields: Dict[str, Any]) -> Optional[str]:
    """Whom the Event concerns: Person, else Assignee (G-34)."""
    for name in (F_PERSON, F_ASSIGNEE):
        v = fields.get(name)
        if isinstance(v, dict) and v.get("account_id"):
            return v["account_id"]
        if isinstance(v, str) and v:
            return v
    return None


def compute(fields: Dict[str, Any], *, status: str, judged_at: Optional[datetime], created_at: datetime,
            started_on: Optional[date] = None, person_known: bool = True) -> Outcome:
    """The Outcome of one Event. `status` is its Jira status; `judged_at` the time of its last
    move to Judged; `started_on` the concerned Person's "Mulai bekerja" date, if any."""
    if status == "Appealed":
        return Outcome("on_hold", reason="Sedang banding")
    if status != "Judged" or judged_at is None:
        return Outcome("not_judged", reason="Belum dinilai")

    missing: List[str] = []
    etype = _one(fields, F_TYPE, missing)
    if not person_known:
        missing.append(F_PERSON)
    if etype is None:
        return Outcome("belum_lengkap", missing=missing)

    kind = "excellence" if etype.lower().startswith("excellence") else "violation"
    if kind == "excellence":
        codes = [v.split(" ")[0].strip().upper() for v in _values(fields.get(F_EXCELLENCE))]
        if not codes or any(c not in EXCELLENCE_POINTS for c in codes):
            missing.append(F_EXCELLENCE)
        if missing:
            return Outcome("belum_lengkap", kind=kind, missing=missing)
        total = sum(EXCELLENCE_POINTS[c] for c in codes)
        out = Outcome("counted", points=total, kind=kind, category=codes[0] if len(codes) == 1 else ", ".join(codes),
                      letter="X", formula=" + ".join(f"{c}: +{EXCELLENCE_POINTS[c]}" for c in codes))
    else:
        # Events carry the Violation Category only (G-50); a Defect Category, if one ever
        # appears on an Event, is used only when no Violation Category is set.
        v_code, v_letter = category(fields.get(F_VIOLATION))
        d_code, d_letter = category(fields.get(F_DEFECT))
        code, letter = (v_code, v_letter) if v_code else (d_code, d_letter)
        if not code:
            missing.append(F_VIOLATION)
        direct = None
        if code == "H2":
            direct = _one(fields, F_DIRECT, [])  # its absence never blocks points, only automation
            direct = direct.split(" ")[0].strip() if direct else None
        judgment = _one(fields, F_JUDGMENT, missing)
        if judgment is not None and judgment.strip().lower() != "kelalaian":
            if missing:
                return Outcome("belum_lengkap", kind=kind, category=code, letter=letter, missing=missing)
            out = Outcome("zero", points=0, kind=kind, category=code, letter=letter, direct_item=direct,
                          reason=judgment, formula=f"Bukan kelalaian ({judgment}): 0")
        else:
            # Several values allowed (G-51): if the client knew, it is 3, else 1.
            observed = [o.lower() for o in _values(fields.get(F_OBSERVER))]
            weight = (3 if any("external" in o or "client" in o or "klien" in o for o in observed)
                      else 1 if any("internal" in o for o in observed) else None)
            if weight is None:
                missing.append(F_OBSERVER)
            own = _one(fields, F_OWN, missing)
            solved = _one(fields, F_SOLVED, missing)
            multiplier = None
            if own is not None and solved is not None:
                if _yes(own) and _yes(solved):
                    multiplier = 0
                else:
                    known = _one(fields, F_KNOWN, missing)
                    if known is not None:
                        multiplier = 2 if _yes(known) else 1
            if missing:
                return Outcome("belum_lengkap", kind=kind, category=code, letter=letter, direct_item=direct,
                               missing=missing)
            points = -10 * weight * multiplier
            who = "Klien" if weight == 3 else "Internal"
            how = {0: "self-report", 1: "ditemukan orang lain", 2: "ditemukan orang lain, sudah tahu"}[multiplier]
            out = Outcome("counted", points=points, kind=kind, category=code, letter=letter, direct_item=direct,
                          formula=f"{who} × {how}")

    # Before v2.1 counts (G-47), and a new staff member's adaptation (G-27; §4.3 applies from day one).
    if judged_at.date() < COUNTING_FROM:
        out.state, out.reason = "reference", "Dinilai sebelum 27 September 2026"
    elif (started_on is not None and started_on <= created_at.date() < started_on + timedelta(days=ADAPTATION_DAYS)
          and out.category != "H2"):
        out.state, out.reason = "adaptation", "Masa adaptasi 30 hari"
        out.points = 0
    return out


def comment_text(out: Outcome) -> str:
    """The one comment the Hub posts on a judged Event (G-45)."""
    sign = f"{out.points:+d}" if out.points else "0"
    line = f"Poin: {sign}" + (f" ({out.formula})" if out.formula else "")
    if out.state == "reference":
        line += ". Dinilai sebelum Incentive Framework v2.1 berlaku (27 September 2026), jadi hanya sebagai catatan dan tidak dihitung."
    elif out.state == "adaptation":
        line = "Poin: 0 (masa adaptasi 30 hari; tetap dicatat sebagai bahan belajar)."
    return line + "\n— dihitung otomatis oleh Noktah Hub"
