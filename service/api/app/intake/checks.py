"""
Deterministic checks on each Intake Proposal (research R3). No AI here: the model
drafts, these rules check, and a Manager decides (constitution XI, G-4, G-5).

Flags (the only values `intake_proposals.flags` accepts):
  unverified        text source, and the excerpt is not in the submitted text (FR-032)
  from_image        drawn from a screenshot or a scanned PDF (FR-033)
  not_pic           a FACT whose speaker is not the Client's PIC, or is unknown (FR-034, G-5)
  price_incomplete  a Harga & promo line without its unit or its conditions
  other_client      the excerpt names another Client/branch, or a name the card says never to write
  contradicts       differs from the current value without being stated as an update
  not_bahasa        the excerpt is plainly English; shown as is, never translated (G-13)

Acceptance (`check_decision`): `from_image` needs the tick `image_checked`,
`not_pic` needs `pic_confirmed`, and an `unverified` Proposal cannot be accepted
as is — it must be edited (its value retyped) or rejected.
"""
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

FLAG_ORDER = ("unverified", "from_image", "not_pic", "price_incomplete", "other_client", "contradicts", "not_bahasa")
TICK_FOR_FLAG = {"from_image": "image_checked", "not_pic": "pic_confirmed"}
FACT_TARGETS = ("profil", "guideline")
# An excerpt this short matches almost any text, so it proves nothing.
MIN_EXCERPT_CHARS = 5

# WhatsApp export prefixes, stripped before matching:
#   [25/09/26 10.15] Rina:          (desktop copy)
#   [25/09/26, 10.15.33] Rina:      (iOS export)
#   25/09/26 10.15 - Rina:          (Android export)
_WA_PREFIX = re.compile(
    r"^\s*\[?\d{1,2}/\d{1,2}/\d{2,4},?\s+\d{1,2}[.:]\d{2}(?:[.:]\d{2})?(?:\s*[AaPp][Mm])?\]?\s*(?:-\s*)?"
    r"[^:\n]{1,60}:\s")
_QUOTES = str.maketrans({"“": '"', "”": '"', "„": '"', "«": '"', "»": '"',
                         "‘": "'", "’": "'", "‚": "'", "`": "'",
                         "–": "-", "—": "-", "…": "..."})
_ZERO_WIDTH = re.compile(r"[​-‏⁠﻿]")
_WORD = re.compile(r"[a-z0-9]+")

HONORIFICS = {"pak", "bapak", "bu", "ibu", "mas", "mbak", "mba", "kak", "dr", "drg", "dok", "dokter", "sdr", "sdri",
              "h", "hj", "ny", "tn", "bang", "om", "tante"}

_ID_WORDS = {"yang", "dan", "di", "ke", "dari", "untuk", "dengan", "ini", "itu", "tidak", "akan", "ada", "kami",
             "kita", "ya", "juga", "atau", "pada", "sudah", "bisa", "harga", "mohon", "tolong", "jadi", "karena",
             "per", "saja", "agar", "supaya", "lebih", "setiap", "buat", "sama", "kalau", "jangan", "boleh"}
_EN_WORDS = {"the", "and", "of", "to", "is", "are", "for", "with", "this", "that", "our", "we", "you", "will",
             "be", "on", "in", "it", "as", "by", "from", "an", "should", "must", "can", "not", "their", "which"}


# ── text normalisation ────────────────────────────────────────────────────────

def strip_wa_prefix(line: str) -> str:
    return _WA_PREFIX.sub("", line, count=1)


def match_key(text: str) -> str:
    """Lowercase, one quote style, WhatsApp prefixes gone, whitespace collapsed."""
    text = unicodedata.normalize("NFKC", text or "")
    text = _ZERO_WIDTH.sub("", text).translate(_QUOTES)
    text = "\n".join(strip_wa_prefix(line) for line in text.split("\n"))
    return re.sub(r"\s+", " ", text).strip().lower()


def words(text: str) -> List[str]:
    return _WORD.findall(match_key(text))


def quote_found(excerpt: Optional[str], source_text: Optional[str]) -> bool:
    needle = match_key(excerpt or "")
    if len(needle) < MIN_EXCERPT_CHARS:
        return False
    return needle in match_key(source_text or "")


# ── people and names ──────────────────────────────────────────────────────────

def name_tokens(name: Optional[str]) -> Set[str]:
    return {w for w in words(name or "") if w not in HONORIFICS}


def speaker_is_pic(speaker: Optional[str], pic_name: Optional[str]) -> bool:
    """Token-subset either way ('Bu Rina' ↔ 'Rina Wati'). Unknown speaker or no PIC → False."""
    s, p = name_tokens(speaker), name_tokens(pic_name)
    if not s or not p:
        return False
    return s <= p or p <= s


def generic_tokens(all_names: Iterable[str]) -> Set[str]:
    """Words shared by 2+ Client names ('klinik', 'mata', 'utama'): they identify nobody."""
    counts: Dict[str, int] = {}
    for name in all_names:
        for t in name_tokens(name):
            counts[t] = counts.get(t, 0) + 1
    return {t for t, n in counts.items() if n >= 2}


def names_other_client(text: str, client_name: str, other_names: Iterable[str], generic: Set[str],
                       never_write: Iterable[str] = ()) -> bool:
    """True when `text` names another Client or a sibling branch, or a name this card forbids.

    Another Client is named when ALL of its distinctive words (its name minus this
    Client's words and minus generic words) appear. For sibling branches that is the
    place name: 'Klinik Mata Sampang' is named by 'Sampang' on Bireuen's card.
    """
    have = set(words(text))
    key = match_key(text)
    for phrase in never_write:
        p = match_key(phrase)
        if len(p) >= 3 and re.search(rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])", key):
            return True
    own = name_tokens(client_name)
    for other in other_names:
        distinctive = name_tokens(other) - own - generic
        if distinctive and distinctive <= have:
            return True
    return False


# ── values ────────────────────────────────────────────────────────────────────

def _is_blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def price_incomplete(field_key: Optional[str], value: Any) -> bool:
    """A Harga & promo line with a price but no unit or no conditions is not a fact yet."""
    if field_key != "harga_promo" or not isinstance(value, list):
        return False
    for line in value:
        if isinstance(line, dict) and not _is_blank(line.get("harga")):
            if _is_blank(line.get("satuan")) or _is_blank(line.get("syarat")):
                return True
    return False


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def value_text(value: Any) -> str:
    """Every string inside a value, joined (for name checks)."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(value_text(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(value_text(v) for v in value)
    return ""


def contradicts(current: Any, proposed: Any, is_update: bool) -> bool:
    if is_update or current is None or _is_blank(value_text(current)):
        return False
    return canonical(current) != canonical(proposed)


def plainly_english(text: str) -> bool:
    """Code-mixing is normal here; only a clearly English passage is flagged."""
    ws = words(text)
    if len(ws) < 5:
        return False
    en = sum(1 for w in ws if w in _EN_WORDS)
    idn = sum(1 for w in ws if w in _ID_WORDS)
    return en >= 3 and en > 2 * idn


# ── putting it together ───────────────────────────────────────────────────────

@dataclass
class Context:
    client_name: str
    source_text: Optional[str]            # None for image sources (no quote check possible)
    is_image: bool
    pic_name: Optional[str]
    other_client_names: List[str] = field(default_factory=list)
    generic: Set[str] = field(default_factory=set)
    never_write: List[str] = field(default_factory=list)
    current: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # {'profil': {key: value}, 'guideline': {...}}


def flags_for(item: Dict[str, Any], ctx: Context) -> List[str]:
    target, key = item.get("target"), item.get("field_key")
    excerpt = item.get("excerpt") or ""
    value = item.get("value")
    found: Set[str] = set()

    if ctx.is_image:
        found.add("from_image")
    elif not quote_found(excerpt, ctx.source_text):
        found.add("unverified")
    if target in FACT_TARGETS and not speaker_is_pic(item.get("speaker"), ctx.pic_name):
        found.add("not_pic")
    if target == "profil" and price_incomplete(key, value):
        found.add("price_incomplete")
    if names_other_client(f"{excerpt}\n{value_text(value)}", ctx.client_name, ctx.other_client_names, ctx.generic,
                          ctx.never_write):
        found.add("other_client")
    if target in FACT_TARGETS and contradicts(ctx.current.get(target, {}).get(key), value, bool(item.get("is_update"))):
        found.add("contradicts")
    if plainly_english(excerpt):
        found.add("not_bahasa")
    return [f for f in FLAG_ORDER if f in found]


def check_decision(flags: Iterable[str], outcome: str, ticks: Dict[str, Any], proposed: Any,
                   final_value: Any) -> None:
    """Raise ValueError (Bahasa) when a decision breaks the acceptance rules (R3)."""
    flags = set(flags)
    if outcome not in ("accept", "edit", "reject"):
        raise ValueError("Keputusan harus accept, edit, atau reject.")
    if outcome == "reject":
        return
    missing = [TICK_FOR_FLAG[f] for f in FLAG_ORDER if f in flags and f in TICK_FOR_FLAG
               and ticks.get(TICK_FOR_FLAG[f]) is not True]
    if missing:
        labels = {"image_checked": "Sudah dicek manual", "pic_confirmed": "PIC sudah konfirmasi"}
        raise ValueError("Centang dulu: " + ", ".join(labels[m] for m in missing) + ".")
    if outcome == "edit" and final_value is None:
        raise ValueError("Isi nilai yang sudah diedit.")
    if "unverified" in flags:
        if outcome != "edit":
            raise ValueError("Kutipan tidak ditemukan di teks. Ketik ulang nilainya (Edit) atau tolak.")
