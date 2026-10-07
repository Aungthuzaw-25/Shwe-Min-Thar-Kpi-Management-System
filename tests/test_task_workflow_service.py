import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.modules.tasks.models.task import TaskStatus
from app.modules.tasks.schemas import TaskCreate, TaskReviewInput
from app.modules.tasks.service import create_task, review_task_l1, submit_task, verify_task_l0


class FakeResult:
    def __init__(self, value=None):
        self._value = value

    def scalars(self):
        return self

    def unique(self):
        return self

    def first(self):
        return self._value


class FakeDB:
    def __init__(self, task=None):
        self.task = task
        self.actor = SimpleNamespace(
            id="reviewer-1",
            is_deleted=False,
            is_active=True,
            role=SimpleNamespace(
                name="Admin",
                permissions=[SimpleNamespace(code="tasks.approve")],
            ),
            user_departments=[
                SimpleNamespace(
                    department_id="dept-1",
                    user_level="L1",
                    is_deleted=False,
                )
            ],
        )
        self.execute_count = 0
        self.added = []
        self.committed = 0
        self.rolled_back = 0

    async def execute(self, stmt):
        self.execute_count += 1
        if self.execute_count > 1 and self.task and self.task.status == TaskStatus.PENDING_L0:
            self.actor.user_departments[0].user_level = "L0"
            self.actor.id = "manager-1"
        return FakeResult(self.task if self.execute_count == 1 else self.actor)

    async def commit(self):
        self.committed += 1

    async def rollback(self):
        self.rolled_back += 1

    async def refresh(self, obj):
        return None

    def add(self, obj):
        self.added.append(obj)


def test_create_task_starts_in_draft_status():
    db = FakeDB()
    payload = TaskCreate(title="Prepare KPI sprint", description="Create report", category="Operations")

    task = asyncio.run(create_task(db, payload, "user-1"))

    assert task.title == "Prepare KPI sprint"
    assert task.status == TaskStatus.DRAFT
    assert db.committed == 1


def test_submit_task_moves_task_to_l1_review():
    task = SimpleNamespace(id="task-1", owner_id="user-1", status=TaskStatus.DRAFT)
    db = FakeDB(task)

    result = asyncio.run(submit_task(db, "task-1", "user-1"))

    assert result.status == TaskStatus.PENDING_L1
    assert db.committed == 1


def test_l1_approval_promotes_task_to_l0_review(monkeypatch):
    task = SimpleNamespace(
        id="task-1",
        title="KPI review",
        status=TaskStatus.PENDING_L1,
        owner_id="user-1",
        owner=SimpleNamespace(
            user_departments=[
                SimpleNamespace(department_id="dept-1", is_deleted=False)
            ]
        ),
        due_date=None,
    )
    db = FakeDB(task)

    async def fake_notification(*args, **kwargs):
        return None

    async def fake_recalc(*args, **kwargs):
        return None

    monkeypatch.setattr("app.modules.tasks.service.create_notification", fake_notification)
    monkeypatch.setattr("app.modules.tasks.service.trigger_kpi_recalculation", fake_recalc)

    payload = TaskReviewInput(action="APPROVED", comments="Good work")
    result = asyncio.run(review_task_l1(db, "task-1", payload, "reviewer-1"))

    assert result.status == TaskStatus.PENDING_L0
    assert db.committed == 1


def test_l0_approval_marks_task_complete(monkeypatch):
    task = SimpleNamespace(
        id="task-1",
        title="Finance report",
        status=TaskStatus.PENDING_L0,
        owner_id="user-1",
        owner=SimpleNamespace(user_departments=[SimpleNamespace(department_id="dept-1")]),
        due_date=None,
    )
    db = FakeDB(task)
    calls = []

    async def fake_notification(*args, **kwargs):
        return None

    async def fake_recalc(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr("app.modules.tasks.service.create_notification", fake_notification)
    monkeypatch.setattr("app.modules.tasks.service.trigger_kpi_recalculation", fake_recalc)

    payload = TaskReviewInput(action="APPROVED", comments="Approved by management")
    result = asyncio.run(verify_task_l0(db, "task-1", payload, "manager-1"))

    assert result.status == TaskStatus.COMPLETED
    assert calls and calls[0]["user_id"] == "user-1"
    assert db.committed == 1


def test_reject_requires_comment():
    task = SimpleNamespace(id="task-1", status=TaskStatus.PENDING_L1)
    db = FakeDB(task)

    with pytest.raises(HTTPException):
        asyncio.run(review_task_l1(db, "task-1", TaskReviewInput(action="REJECTED", comments=""), "reviewer-1"))
