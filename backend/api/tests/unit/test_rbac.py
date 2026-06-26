"""RBAC role hierarchy tests."""
from auth.rbac import ROLE_HIERARCHY


def test_hierarchy_order():
    assert ROLE_HIERARCHY["FIELD_WORKER"] < ROLE_HIERARCHY["PHC_ADMIN"]
    assert ROLE_HIERARCHY["PHC_ADMIN"] < ROLE_HIERARCHY["DISTRICT_OFFICER"]
    assert ROLE_HIERARCHY["DISTRICT_OFFICER"] < ROLE_HIERARCHY["STATE_ADMIN"]
    assert ROLE_HIERARCHY["STATE_ADMIN"] < ROLE_HIERARCHY["SUPERADMIN"]


def test_all_roles_present():
    expected = {"FIELD_WORKER", "PHC_ADMIN", "DISTRICT_OFFICER", "STATE_ADMIN", "SUPERADMIN"}
    assert expected == set(ROLE_HIERARCHY.keys())
