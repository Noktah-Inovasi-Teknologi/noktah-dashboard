"""
The Content workflow as Stations (spec 009 G-14, G-19; research R7). Pure: no I/O.

Each Jira status has an order in the workflow and belongs to one Station, lettered as in the
Incentive Framework. A move to a lower order is a Return. Statuses renamed in Jira keep their
place (the history still carries the old names). An unknown status is reported, never guessed.
"""
from typing import Dict, Optional, Tuple

STATIONS: Dict[str, Dict[str, str]] = {
    "A": {"label": "Planning", "role": "Content Planner", "role_key": "content_planner"},
    "B": {"label": "Footage", "role": "Field Associate", "role_key": "field_associate"},
    "C": {"label": "Editing", "role": "Content Editor", "role_key": "content_editor"},
    "D": {"label": "Quality control", "role": "Quality Assurance", "role_key": "quality_assurance"},
    "E": {"label": "Client & publication", "role": "Field Associate", "role_key": "field_associate"},
}

# status → (order, station); Done ends the workflow; Shelved is cancelled.
WORKFLOW: Dict[str, Tuple[int, Optional[str]]] = {
    "plan": (1, "A"),
    "footages in progress": (2, "B"),
    "footage taken": (3, "C"),
    "designs in progress": (4, "C"),
    "designs in review": (5, "D"),
    "internally reviewed": (6, "E"),
    "in review by client": (7, "E"),
    "reviewed by client": (8, "E"),
    "scheduled": (9, "E"),
    "published, need review": (10, "D"),
    "done": (11, None),
}
ALIASES = {"reviewed": "internally reviewed", "published & reviewed": "published, need review"}

PUBLISHED = {"published, need review", "done"}
CANCELLED = {"shelved"}
# On Hold and Shelved are managers' decisions and mean what they say (G-52): neither is a
# Station, so time there is charged to no one, and neither is ever Late.
ON_HOLD = {"on hold"}
QA_ENTRY = "designs in review"
CLIENT_REVIEW = {"in review by client", "reviewed by client"}
CLIENT_REVIEW_START = "in review by client"


def canon(status: Optional[str]) -> str:
    s = " ".join((status or "").lower().split())
    return ALIASES.get(s, s)


def place(status: Optional[str]) -> Optional[Tuple[int, Optional[str]]]:
    """(order, station) of a status; None if unknown (Shelved included: it has no place)."""
    return WORKFLOW.get(canon(status))


def station_of(status: Optional[str]) -> Optional[str]:
    p = place(status)
    return p[1] if p else None


def is_known(status: Optional[str]) -> bool:
    return canon(status) in WORKFLOW or canon(status) in CANCELLED or canon(status) in ON_HOLD


def is_published(status: Optional[str]) -> bool:
    return canon(status) in PUBLISHED


def is_cancelled(status: Optional[str]) -> bool:
    return canon(status) in CANCELLED


def is_on_hold(status: Optional[str]) -> bool:
    return canon(status) in ON_HOLD


def is_return(from_status: Optional[str], to_status: Optional[str]) -> bool:
    """A move back to an earlier point of the workflow (a move to Shelved is not a Return)."""
    a, b = place(from_status), place(to_status)
    return a is not None and b is not None and b[0] < a[0]
