from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.tasks.workflow_repo import submit_task_batch


class FakeScalars:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return FakeScalars(self.rows)


class FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.statement = None
        self.committed = False

    async def execute(self, statement):
        self.statement = statement
        return FakeResult(self.rows)

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_batch_submission_locks_rows_and_rejects_partial_selection():
    requested_ids = [uuid4(), uuid4()]
    db = FakeDB([SimpleNamespace(id=requested_ids[0])])

    with pytest.raises(ValueError, match="Task"):
        await submit_task_batch(db, requested_ids, uuid4(), uuid4())

    assert db.statement._for_update_arg is not None
    assert db.committed is False
