from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.dependencies import has_effective_permission
from app.modules.tasks import router as tasks_router


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


def test_department_user_level_controls_workflow_permissions():
    permission = make_permission("tasks.approve")
    l2 = SimpleNamespace(
        role=SimpleNamespace(name="Department User", permissions=[permission]),
        permission_overrides=[],
        user_departments=[SimpleNamespace(user_level="L2", is_deleted=False)],
        id="user-1",
    )
    l1 = SimpleNamespace(
        role=SimpleNamespace(name="Department User", permissions=[permission]),
        permission_overrides=[],
        user_departments=[SimpleNamespace(user_level="L1", is_deleted=False)],
        id="user-2",
    )

    assert has_effective_permission(l2, "tasks.approve") is False
    assert has_effective_permission(l1, "tasks.approve") is True


def test_department_user_level_normalization_supports_prefixed_levels():
    permission = make_permission("tasks.approve")
    l0 = SimpleNamespace(
        role=SimpleNamespace(name="Department User", permissions=[permission]),
        permission_overrides=[],
        user_departments=[SimpleNamespace(user_level="Level 0", is_deleted=False)],
        id="user-0",
    )

    assert has_effective_permission(l0, "tasks.approve") is True


@pytest.mark.asyncio
async def test_admin_task_detail_uses_department_scope(monkeypatch):
    task = SimpleNamespace(
        id="task-a",
        owner_id="owner-a",
        assigner_id="assigner-a",
        reviews=[],
    )

    class ScalarResult:
        def first(self):
            return task

    class Result:
        def scalars(self):
            return ScalarResult()

    class Database:
        async def execute(self, statement):
            return Result()

    called = False

    async def deny_cross_department_task(db, task_id, current_user):
        nonlocal called
        called = True
        raise HTTPException(status_code=404, detail="Task not found.")

    monkeypatch.setattr(tasks_router, "_authorized_task", deny_cross_department_task)
    admin = SimpleNamespace(role=SimpleNamespace(name="Admin"))

    with pytest.raises(HTTPException) as error:
        await tasks_router.get_single_task("task-a", Database(), admin)

    assert called is True
    assert error.value.status_code == 404
