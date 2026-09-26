"""
What a Person may do, in which Noktah Brand. Pure: no I/O, no framework.

A Person has three separate parameters (migration 012):

  Units        Noktah (the company group), Eskala, Venyu. Clients belong to a
               brand; the group covers every brand.
  roles        from the central catalog (`unit_roles`), each in one Unit. A role
               says what someone IS; it grants nothing by itself, except Owner.
  permissions  what they may DO in the Hub (PERMISSIONS below). A new role
               suggests its catalog defaults; the stored set is the authority.

A permission applies in the brands of the Person's Units, or in every brand when
one of their Units is the Noktah group. The Owner role may do everything,
everywhere, whatever its stored permissions say, so the company can't lock itself out.

`can()` answers ALLOW, DENY or NEEDS_APPROVAL. Only `edit_guideline` can return
NEEDS_APPROVAL: `edit_profil` without `approve_guideline` stores the change as
pending, for someone with `approve_guideline` to decide (G-12).
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import FrozenSet, Iterable, Optional, Set, Tuple

GROUP_UNIT = "noktah"

# In the order the Hub lists them. `hub_access` is implied by any other.
PERMISSIONS = ("hub_access", "edit_clients", "edit_profil", "approve_guideline", "edit_requests",
               "run_intake", "manage_people", "manage_automation", "view_reports", "view_incentive")

# Roles only the Owner may grant or end.
OWNER_ONLY_ROLES = {"owner", "brand_manager"}


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
    # Otomasi & Laporan (spec 009); checked against brand "eskala"
    MANAGE_AUTOMATION = "manage_automation"
    VIEW_REPORTS = "view_reports"
    VIEW_INCENTIVE = "view_incentive"


_NEEDS = {
    Action.READ: "hub_access",
    Action.EDIT_REGISTRY: "edit_clients",
    Action.EDIT_PROFIL: "edit_profil",
    Action.APPROVE_GUIDELINE: "approve_guideline",
    Action.EDIT_REQUESTS: "edit_requests",
    Action.RUN_INTAKE: "run_intake",
    Action.MANAGE_PEOPLE: "manage_people",
    Action.MANAGE_AUTOMATION: "manage_automation",
    Action.VIEW_REPORTS: "view_reports",
    Action.VIEW_INCENTIVE: "view_incentive",
}


@dataclass(frozen=True)
class Assignment:
    """One active role held by a Person, in one Unit."""
    role: str
    brand: Optional[str]


@dataclass(frozen=True)
class Access:
    """Everything the rules below need to know about a Person."""
    assignments: Tuple[Assignment, ...] = ()
    units: FrozenSet[str] = field(default_factory=frozenset)
    permissions: FrozenSet[str] = field(default_factory=frozenset)


def normalize_permissions(perms: Iterable[str]) -> Set[str]:
    """Any permission implies hub_access: it can't be used without signing in."""
    perms = set(perms)
    if perms:
        perms.add("hub_access")
    return perms


def is_owner(access: Access) -> bool:
    return any(a.role == "owner" for a in access.assignments)


def is_manager(access: Access) -> bool:
    """May this Person sign in to the Hub at all? (G-11)"""
    return is_owner(access) or "hub_access" in access.permissions


def covers_everything(access: Access) -> bool:
    return is_owner(access) or GROUP_UNIT in access.units


def in_scope(access: Access, unit: Optional[str]) -> bool:
    """Does the Person's reach include this Unit (a brand, or the group itself)?"""
    if covers_everything(access):
        return True
    return unit is not None and unit in access.units


def visible_brands(access: Access, all_brands: Iterable[str]) -> Set[str]:
    """Noktah Brands whose Clients this Person may see (G-10)."""
    if not is_manager(access):
        return set()
    return {b for b in all_brands if in_scope(access, b)}


def can(access: Access, action: Action, brand: Optional[str]) -> Decision:
    """May the Person do `action` in `brand`?

    `brand` is the Noktah Brand of the thing acted on; None for company-wide
    actions, which only the Owner may take.
    """
    if is_owner(access):
        return Decision.ALLOW
    if action is Action.APPOINT_BRAND_MANAGER or brand is None or not in_scope(access, brand):
        return Decision.DENY
    perms = access.permissions
    if action is Action.EDIT_GUIDELINE:
        if "approve_guideline" in perms:
            return Decision.ALLOW
        return Decision.NEEDS_APPROVAL if "edit_profil" in perms else Decision.DENY
    return Decision.ALLOW if _NEEDS[action] in perms else Decision.DENY


def may_manage(access: Access) -> bool:
    return is_owner(access) or "manage_people" in access.permissions


def may_grant(access: Access, role: str, unit: Optional[str]) -> bool:
    """May the Person grant or end `role` in `unit`?

    The Owner: any role. Anyone else with manage_people: any role in a Unit within
    their reach, except Owner and Brand Manager.
    """
    if is_owner(access):
        return True
    return may_manage(access) and role not in OWNER_ONLY_ROLES and in_scope(access, unit)


def may_set_unit(access: Access, unit: str) -> bool:
    """May the Person add someone to, or take them out of, `unit`?"""
    return is_owner(access) or (may_manage(access) and in_scope(access, unit))


def may_give_permission(access: Access, permission: str) -> bool:
    """Nobody hands out a permission they don't hold themselves (the Owner holds all)."""
    if permission not in PERMISSIONS:
        return False
    return is_owner(access) or (may_manage(access) and permission in access.permissions)
