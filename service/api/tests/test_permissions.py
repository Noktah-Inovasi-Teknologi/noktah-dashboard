"""The permission rules (migration 012), checked exhaustively: permission × action × own/other brand."""
import pytest

from app.permissions import (
    PERMISSIONS, Access, Action, Assignment, Decision, can, is_manager, may_give_permission, may_grant,
    may_set_unit, normalize_permissions, visible_brands,
)

A = Action
ALLOW, DENY, WAIT = Decision.ALLOW, Decision.DENY, Decision.NEEDS_APPROVAL
BRANDS = ["eskala", "venyu"]
EDITOR = {"hub_access", "edit_clients", "edit_profil", "edit_requests", "run_intake"}  # PM / AE defaults


def person(units=("eskala",), perms=(), roles=()):
    return Access(assignments=tuple(Assignment(r, u) for r, u in roles), units=frozenset(units),
                  permissions=frozenset(perms))


OWNER = person(("noktah",), (), [("owner", "noktah")])

# the one permission each action needs (EDIT_GUIDELINE and APPOINT are checked separately)
NEEDS = {A.READ: "hub_access", A.EDIT_REGISTRY: "edit_clients", A.EDIT_PROFIL: "edit_profil",
         A.APPROVE_GUIDELINE: "approve_guideline", A.EDIT_REQUESTS: "edit_requests",
         A.RUN_INTAKE: "run_intake", A.MANAGE_PEOPLE: "manage_people"}


@pytest.mark.parametrize("action", sorted(NEEDS, key=lambda a: a.value))
def test_each_action_needs_its_permission_in_its_brand(action):
    assert can(person(perms={NEEDS[action]}), action, "eskala") is ALLOW
    assert can(person(perms=set(PERMISSIONS) - {NEEDS[action]}), action, "eskala") is DENY
    assert can(person(perms=set(PERMISSIONS)), action, "venyu") is DENY, "another brand"


def test_guideline_edits_wait_without_approve_guideline():
    assert can(person(perms=EDITOR), A.EDIT_GUIDELINE, "eskala") is WAIT
    assert can(person(perms=EDITOR | {"approve_guideline"}), A.EDIT_GUIDELINE, "eskala") is ALLOW
    assert can(person(perms={"hub_access"}), A.EDIT_GUIDELINE, "eskala") is DENY


@pytest.mark.parametrize("action", list(Action))
def test_owner_may_do_everything_everywhere(action):
    assert can(OWNER, action, "venyu") is ALLOW
    assert can(OWNER, action, None) is ALLOW


def test_only_the_owner_appoints_and_acts_company_wide():
    everything = person(("noktah",), PERMISSIONS)
    assert can(everything, A.APPOINT_BRAND_MANAGER, "eskala") is DENY
    assert can(everything, A.READ, None) is DENY


def test_the_noktah_group_reaches_every_brand():
    sales = person(("noktah",), {"hub_access"}, [("sales_marketing", "noktah")])
    assert visible_brands(sales, BRANDS) == {"eskala", "venyu"}
    assert can(sales, A.READ, "venyu") is ALLOW
    assert can(sales, A.EDIT_REGISTRY, "venyu") is DENY


def test_a_role_alone_grants_nothing():
    bm_without_permissions = person(roles=[("brand_manager", "eskala")])
    assert not is_manager(bm_without_permissions)
    assert visible_brands(bm_without_permissions, BRANDS) == set()
    assert can(bm_without_permissions, A.READ, "eskala") is DENY


def test_signing_in_needs_hub_access_or_owner():
    assert not is_manager(person())
    assert not is_manager(person(roles=[("content_editor", "eskala")]))
    assert is_manager(person(perms={"hub_access"}))
    assert is_manager(OWNER)


def test_any_permission_implies_hub_access():
    assert normalize_permissions({"run_intake"}) == {"run_intake", "hub_access"}
    assert normalize_permissions(set()) == set()


def test_units_in_two_brands():
    both = person(("eskala", "venyu"), EDITOR)
    assert visible_brands(both, BRANDS) == {"eskala", "venyu"}
    assert can(both, A.EDIT_REGISTRY, "venyu") is ALLOW


def test_granting_roles_and_units():
    manager = person(perms={"hub_access", "manage_people"}, roles=[("brand_manager", "eskala")])
    assert may_grant(manager, "account_executive", "eskala")
    assert may_grant(manager, "content_editor", "eskala")
    assert not may_grant(manager, "production_manager", "venyu"), "outside their Units"
    assert not may_grant(manager, "brand_manager", "eskala")
    assert not may_grant(manager, "owner", "noktah")
    assert may_set_unit(manager, "eskala") and not may_set_unit(manager, "noktah")
    assert may_grant(OWNER, "brand_manager", "venyu")
    assert not may_grant(person(perms=EDITOR), "quality_assurance", "eskala"), "no manage_people"


def test_permissions_are_given_only_by_someone_who_holds_them():
    manager = person(perms={"hub_access", "manage_people", "edit_clients"})
    assert may_give_permission(manager, "edit_clients")
    assert not may_give_permission(manager, "approve_guideline")
    assert may_give_permission(OWNER, "approve_guideline")
    assert not may_give_permission(OWNER, "launch_rockets")
