import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.modules.tasks import router as tasks_router
from app.modules.tasks.models.task import TaskStatus
from app.modules.tasks.models.task_evidence import TaskEvidence
from app.modules.tasks.models.task_history import TaskHistory


class _Result:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return self

    def first(self):
        return self.value


class _Session:
    def __init__(self, evidence, task):
        self.evidence = evidence
        self.task = task
        self.added = []
        self.deleted = []
        self.events = []

    async def execute(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        if entity is TaskEvidence:
            return _Result(self.evidence)
        if entity is tasks_router.Task:
            return _Result(self.task)
        raise AssertionError(f"Unexpected query: {entity}")

    def add(self, value):
        self.added.append(value)

    async def delete(self, value):
        self.deleted.append(value)
        self.events.append("database-delete")

    async def commit(self):
        self.events.append("commit")

    async def rollback(self):
        self.events.append("rollback")


def _evidence_and_task(status=TaskStatus.DRAFT):
    evidence = SimpleNamespace(
        id="evidence-1",
        task_id="task-1",
        uploaded_by="owner-1",
        file_path="task_evidence/evidence.pdf",
        file_name="evidence.pdf",
    )
    task = SimpleNamespace(
        id="task-1",
        owner_id="owner-1",
        title="Monthly report",
        status=status,
    )
    user = SimpleNamespace(
        id="owner-1",
        role=SimpleNamespace(name="Department User"),
    )
    return evidence, task, user


def test_owner_deletes_evidence_with_history_before_storage_cleanup(monkeypatch):
    evidence, task, user = _evidence_and_task()
    db = _Session(evidence, task)
    storage_events = []

    async def delete_file(_object_name):
        storage_events.append("storage-delete")
        assert db.events == ["database-delete", "commit"]

    monkeypatch.setattr(tasks_router.storage_service, "delete_file", delete_file)

    result = asyncio.run(tasks_router.delete_task_evidence(evidence.id, db, user))

    assert result["message"] == "Evidence file removed successfully."
    assert db.events == ["database-delete", "commit"]
    assert storage_events == ["storage-delete"]
    assert any(isinstance(item, TaskHistory) for item in db.added)


def test_non_owner_cannot_delete_evidence_after_task_submission(monkeypatch):
    evidence, task, _ = _evidence_and_task(TaskStatus.PENDING_L1)
    reviewer = SimpleNamespace(
        id="reviewer-1",
        role=SimpleNamespace(name="Department User"),
    )
    db = _Session(evidence, task)
    storage_called = False

    async def delete_file(_object_name):
        nonlocal storage_called
        storage_called = True

    monkeypatch.setattr(tasks_router.storage_service, "delete_file", delete_file)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(tasks_router.delete_task_evidence(evidence.id, db, reviewer))

    assert exc_info.value.status_code == 403
    assert not storage_called
    assert "commit" not in db.events


def test_storage_cleanup_failure_is_reported_after_database_audit(monkeypatch):
    evidence, task, user = _evidence_and_task()
    db = _Session(evidence, task)

    async def fail_delete(_object_name):
        raise RuntimeError("object store unavailable")

    monkeypatch.setattr(tasks_router.storage_service, "delete_file", fail_delete)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(tasks_router.delete_task_evidence(evidence.id, db, user))

    assert exc_info.value.status_code == 500
    assert db.events == ["database-delete", "commit"]
    assert any(isinstance(item, TaskHistory) for item in db.added)
