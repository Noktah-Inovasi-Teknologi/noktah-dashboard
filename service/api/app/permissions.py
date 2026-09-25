"""
What a role may do, in which Noktah Brand. Pure: no I/O, no framework.

The table is G-9 (grill ledger), scoped by G-10:

  Owner              whole company   everything, every Noktah Brand; appoints Brand Managers
  Brand Manager      own Noktah Brand everything there, incl. approving Guideline changes and
                                     granting roles (never brand_manager/owner)
  Project Manager,   own Noktah Brand edit Clients, teams, accounts, Client Cards; run Intake.
  Account Executive                  Guideline changes need the Brand Manager's approval
  Sales & Marketing  own Noktah Brand read only
  staff roles        —               cannot sign in at all

`can()` answers ALLOW, DENY or NEEDS_APPROVAL. Only `edit_guideline` can return
NEEDS_APPROVAL: the change is stored as pending and waits for the Brand Manager
(or the Owner, G-12).
"""
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional, Set


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    NEEDS_APPROVAL = "needs_approval"


class Action(str, Enum):
    READ = "read"
    EDIT_REGISTRY = "edit_registry"
    EDIT_PROFIL = "edit_profil"
    EDIT_GUIDELINE = "edit_guideline"
    APPROVE_GUIDELINE = "approve_guideline"
    EDIT_REQUESTS = "edit_requests"
    RUN_INTAKE = "run_intake"
    MANAGE_PEOPLE = "manage_people"
    APPOINT_BRAND_MANAGER = "appoint_brand_manager"


MANAGER_ROLES = {"owner", "brand_manager", "project_manager", "account_executive", "sales_marketing"}
STAFF_ROLES = {"content_planner", "field_associate", "content_editor", "qc"}
ALL_ROLES = MANAGER_ROLES | STAFF_ROLES

_EDITOR_ACTIONS = {
    Action.READ, Action.EDIT_REGISTRY, Action.EDIT_PROFIL, Action.EDIT_REQUESTS, Action.RUN_INTAKE,
}


@dataclass(frozen=True)
class Assignment:
    """One active role held by the caller. `brand` is None only for the Owner."""
    role: str
    brand: Optional[str]


def is_manager(assignments: Iterable[Assignment]) -> bool:
    """May this Person sign in to the Hub at all? (G-9, G-11)"""
    return any(a.role in MANAGER_ROLES for a in assignments)


def visible_brands(assignments: Iterable[Assignment], all_brands: Iterable[str]) -> Set[str]:
    """Noktah Brands whose Clients this Person may see (G-10)."""
    assignments = list(assignments)
    if any(a.role == "owner" for a in assignments):
        return set(all_brands)
    return {a.brand for a in assignments if a.role in MANAGER_ROLES and a.brand}


def _one(role: str, action: Action) -> Decision:
    if role == "owner" or role == "brand_manager":
        if action is Action.APPOINT_BRAND_MANAGER and role != "owner":
            return Decision.DENY
        return Decision.ALLOW
    if role in {"project_manager", "account_executive"}:
        if action in _EDITOR_ACTIONS:
            return Decision.ALLOW
        if action is Action.EDIT_GUIDELINE:
            return Decision.NEEDS_APPROVAL
        return Decision.DENY
    if role == "sales_marketing":
        return Decision.ALLOW if action is Action.READ else Decision.DENY
    return Decision.DENY  # staff roles, unknown roles


def can(assignments: Iterable[Assignment], action: Action, brand: Optional[str]) -> Decision:
    """The best decision any of the caller's roles gives for `action` in `brand`.

    `brand` is the Noktah Brand of the thing acted on; None for company-wide
    actions (only the Owner can act company-wide).
    """
    best = Decision.DENY
    for a in assignments:
        if a.role == "owner":
            in_scope = True
        else:
            in_scope = brand is not None and a.brand == brand
        if not in_scope:
            continue
        d = _one(a.role, action)
        if d is Decision.ALLOW:
            return Decision.ALLOW
        if d is Decision.NEEDS_APPROVAL:
            best = Decision.NEEDS_APPROVAL
    return best


def may_grant(assignments: Iterable[Assignment], role: str, brand: Optional[str]) -> bool:
    """May the caller grant `role` in `brand`?

    Owner: any role, incl. brand_manager and owner. Brand Manager: any role in their
    own Noktah Brand except brand_manager and owner. Nobody else.
    """
    assignments = list(assignments)
    if role not in ALL_ROLES:
        return False
    if any(a.role == "owner" for a in assignments):
        return True
    if role in {"owner", "brand_manager"}:
        return False
    return brand is not None and any(a.role == "brand_manager" and a.brand == brand for a in assignments)
