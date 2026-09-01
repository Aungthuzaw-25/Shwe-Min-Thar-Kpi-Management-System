from types import SimpleNamespace

from app.core.dependencies import has_effective_permission


def make_permission(code: str):
    return SimpleNamespace(code=code)


def test_super_admin_has_every_permission():
    user = SimpleNamespace(
        role=SimpleNamespace(name="Super Admin", permissions=[]),
        permission_overrides=[],
        id="user-1",
    )

    assert has_effective_permission(user, "tasks.approve") is True


def test_owner_scope_allow_override_grants_access():
    permission = make_permission("tasks.create")
    user = SimpleNamespace(
        role=SimpleNamespace(name="L2", permissions=[]),
        permission_overrides=[
            SimpleNamespace(permission=permission, allowed=True, scope="OWN", department_id=None)
        ],
        id="user-1",
    )

    assert has_effective_permission(user, "tasks.create", owner_id="user-1") is True


def test_department_scope_deny_override_beats_role_grant():
    permission = make_permission("tasks.approve")
    user = SimpleNamespace(
        role=SimpleNamespace(name="L2", permissions=[permission]),
        permission_overrides=[
            SimpleNamespace(permission=permission, allowed=False, scope="DEPARTMENT", department_id="dept-1")
        ],
        id="user-1",
    )

    assert has_effective_permission(user, "tasks.approve", department_id="dept-1") is False
