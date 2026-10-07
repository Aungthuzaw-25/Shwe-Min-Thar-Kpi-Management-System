import asyncio
from types import SimpleNamespace

from app.modules.kpi.dashboard_router import (
    _completion_percentage,
    _get_scope_user_ids,
    _normalize_role_name,
)


class FakeResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return list(self._values)

    def first(self):
        return self._values[0] if self._values else None


class FakeDB:
    def __init__(self, values):
        self.values = values

    async def execute(self, stmt):
        return FakeResult(self.values)


def test_normalize_role_name_removes_hyphen_and_underscores():
    assert _normalize_role_name("Super-Admin") == "SUPER ADMIN"
    assert _normalize_role_name("level_1") == "LEVEL 1"


def test_super_admin_scope_returns_none():
    user = SimpleNamespace(id="user-1", role=SimpleNamespace(name="Super Admin"))
    db = FakeDB(["dept-1"])

    result = asyncio.run(_get_scope_user_ids(db, user))

    assert result is None


def test_l1_scope_includes_self_and_own_team():
    user = SimpleNamespace(id="user-1", role=SimpleNamespace(name="L1"))
    db = FakeDB(["user-2", "user-3"])

    result = asyncio.run(_get_scope_user_ids(db, user))

    assert "user-2" in result
    assert "user-3" in result
    assert "user-1" in result


def test_completion_percentage_uses_only_lifecycle_total():
    assert _completion_percentage(2, 5) == 40.0
    assert _completion_percentage(2, 0) == 0.0

def test_admin_scope_with_no_active_delegation_is_personal_only():
    user = SimpleNamespace(id="admin-1", role=SimpleNamespace(name="Admin"))
    db = FakeDB([])

    result = asyncio.run(_get_scope_user_ids(db, user))

    assert result == ["admin-1"]


def test_l0_scope_uses_active_department_membership_results():
    user = SimpleNamespace(id="l0-1", role=SimpleNamespace(name="L0"))
    db = FakeDB(["department-user-1", "department-user-2"])

    result = asyncio.run(_get_scope_user_ids(db, user))

    assert result == ["department-user-1", "department-user-2"]


def test_l2_scope_is_personal_only():
    user = SimpleNamespace(id="l2-1", role=SimpleNamespace(name="Employee"))
    db = FakeDB([])

    result = asyncio.run(_get_scope_user_ids(db, user))

    assert result == ["l2-1"]
