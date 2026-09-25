"""The G-9/G-10 roles table, checked exhaustively: every role × action × own/other Noktah Brand."""
import pytest

from app.permissions import (
    Action, Assignment, Decision, MANAGER_ROLES, STAFF_ROLES, can, is_manager, may_grant, visible_brands,
)

A = Action
ALLOW, DENY, WAIT = Decision.ALLOW, Decision.DENY, Decision.NEEDS_APPROVAL

# role -> the decision for each action, in the role's OWN Noktah Brand
EXPECTED = {
    "owner": {a: ALLOW for a in Action},
    "brand_manager": {**{a: ALLOW for a in Action}, A.APPOINT_BRAND_MANAGER: DENY},
    "project_manager": {
        A.READ: ALLOW, A.EDIT_REGISTRY: ALLOW, A.EDIT_PROFIL: ALLOW, A.EDIT_REQUESTS: ALLOW,
        A.RUN_INTAKE: ALLOW, A.EDIT_GUIDELINE: WAIT, A.APPROVE_GUIDELINE: DENY,
        A.MANAGE_PEOPLE: DENY, A.APPOINT_BRAND_MANAGER: DENY,
    },
    "sales_marketing": {**{a: DENY for a in Action}, A.READ: ALLOW},
}
EXPECTED["account_executive"] = EXPECTED["project_manager"]


@pytest.mark.parametrize("role", sorted(EXPECTED))
@pytest.mark.parametrize("action", list(Action))
def test_own_brand(role, action):
    brand = None if role == "owner" else "eskala"
    assert can([Assignment(role, brand)], action, "eskala") is EXPECTED[role][action]


@pytest.mark.parametrize("role", sorted(set(EXPECTED) - {"owner"}))
@pytest.mark.parametrize("action", list(Action))
def test_other_brand_is_always_denied(role, action):
    assert can([Assignment(role, "eskala")], action, "venyu") is DENY


@pytest.mark.parametrize("action", list(Action))
def test_owner_reaches_every_brand(action):
    assert can([Assignment("owner", None)], action, "venyu") is ALLOW


@pytest.mark.parametrize("role", sorted(STAFF_ROLES))
@pytest.mark.parametrize("action", list(Action))
def test_staff_can_do_nothing(role, action):
    assert can([Assignment(role, "eskala")], action, "eskala") is DENY


def test_staff_and_no_role_cannot_sign_in():
    assert not is_manager([])
    assert not is_manager([Assignment("field_associate", "eskala")])
    for role in MANAGER_ROLES:
        assert is_manager([Assignment(role, None if role == "owner" else "eskala")])


def test_best_role_wins_across_several():
    both = [Assignment("sales_marketing", "eskala"), Assignment("account_executive", "eskala")]
    assert can(both, A.EDIT_PROFIL, "eskala") is ALLOW
    assert can(both, A.EDIT_GUIDELINE, "eskala") is WAIT


def test_roles_in_two_brands_are_separate():
    person = [Assignment("brand_manager", "venyu"), Assignment("sales_marketing", "eskala")]
    assert can(person, A.APPROVE_GUIDELINE, "venyu") is ALLOW
    assert can(person, A.APPROVE_GUIDELINE, "eskala") is DENY
    assert visible_brands(person, ["eskala", "venyu"]) == {"eskala", "venyu"}


def test_visible_brands():
    assert visible_brands([Assignment("owner", None)], ["eskala", "venyu"]) == {"eskala", "venyu"}
    assert visible_brands([Assignment("account_executive", "eskala")], ["eskala", "venyu"]) == {"eskala"}
    assert visible_brands([Assignment("qc", "eskala")], ["eskala", "venyu"]) == set()


def test_granting_roles():
    bm = [Assignment("brand_manager", "eskala")]
    assert may_grant(bm, "account_executive", "eskala")
    assert may_grant(bm, "field_associate", "eskala")
    assert not may_grant(bm, "account_executive", "venyu")
    assert not may_grant(bm, "brand_manager", "eskala")
    assert not may_grant(bm, "owner", None)
    owner = [Assignment("owner", None)]
    assert may_grant(owner, "brand_manager", "venyu")
    assert not may_grant([Assignment("project_manager", "eskala")], "qc", "eskala")
    assert not may_grant(owner, "janitor", "eskala")
