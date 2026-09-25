"""
Name and handle rules, ported verbatim from service/prefect/hashmap.py and
flows/common/roster.py so the Hub keys Clients, aliases and accounts EXACTLY as
roster-sync did — a different normalisation would split one Client into two.

Keep in step with those originals until the automations read the Registry
(docs/DEFERRED.md A-1); after that this module is the only copy.
"""
import re
from typing import Dict
from urllib.parse import urlparse

EMPTY_MARKERS = {"", "-", "–", "—", "n/a", "na", "none", "null", "tbd"}

# Cross-source disagreement roster-sync already resolved (roster.py): the Clients
# sheet spells one Sumenep client "Coffee Shop", COMPONENTS "Coffee Space".
SOURCE_NAME_OVERRIDES: Dict[str, str] = {
    "nirwana coffee shop sumenep": "nirwana coffee space sumenep",
}


def is_blank(value: object) -> bool:
    return str(value if value is not None else "").strip().lower() in EMPTY_MARKERS


def normalize_client_key(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def client_name_tokens(name: str) -> set:
    return {t for t in re.split(r"[^\w]+", normalize_client_key(name)) if t}


def is_token_subset_match(a: str, b: str) -> bool:
    """A candidate proposer, never a decider (see hashmap.is_token_subset_match)."""
    ta, tb = client_name_tokens(a), client_name_tokens(b)
    if not ta or not tb:
        return False
    return ta <= tb or tb <= ta


def profile_handle(value: str) -> str:
    """'https://www.instagram.com/lasikindonesia/' → 'lasikindonesia'; '@Handle' → 'handle'."""
    raw = (value or "").strip()
    if is_blank(raw):
        return ""
    if "/" in raw or raw.lower().startswith("http"):
        path = urlparse(raw if "://" in raw else f"https://{raw}").path
        segments = [seg for seg in path.split("/") if seg]
        raw = segments[-1] if segments else ""
    return raw.lstrip("@").strip().lower()


def profile_url(platform: str, handle: str) -> str:
    """The URL form the Clients sheet holds for an own account."""
    if platform == "tiktok":
        return f"https://www.tiktok.com/@{handle}"
    return f"https://www.instagram.com/{handle}/"
