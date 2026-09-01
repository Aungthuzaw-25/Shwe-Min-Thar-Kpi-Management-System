from types import SimpleNamespace

from app.core.dependencies import has_effective_permission


def make_permission(code: str):
    return SimpleNamespace(code=code)


def make_override(permission, *, allowed, scope="ALL", department_id=None):
    return SimpleNamespace(
        permission=permission,
        allowed=allowed,
        scope=scope,
        department_id=department_id,
    )


def test_explicit_deny_beats_role_grant():
    permission = make_permission("tasks.approve")
    user = SimpleNamespace(
        role=SimpleNamespace(name="L2", permissions=[permission]),
        permission_overrides=[make_override(permission, allowed=False)],
        id="user-1",
    )

    assert has_effective_permission(user, "tasks.approve") is False


def test_explicit_allow_grants_permission_not_in_role():
    permission = make_permission("tasks.create")
    user = SimpleNamespace(
        role=SimpleNamespace(name="L2", permissions=[]),
        permission_overrides=[make_override(permission, allowed=True)],
        id="user-1",
    )

    assert has_effective_permission(user, "tasks.create") is True


def test_missing_permission_is_denied_by_default():
    user = SimpleNamespace(
        role=SimpleNamespace(name="L2", permissions=[]),
        permission_overrides=[],
        id="user-1",
    )

    assert has_effective_permission(user, "tasks.delete") is False
