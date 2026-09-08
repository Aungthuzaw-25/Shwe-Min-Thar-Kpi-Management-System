import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.modules.notifications.repo import count_unread_notifications, create_notification
from app.services.activity_service import record_activity


class FakeDB:
    def __init__(self, count=0):
        self.added = []
        self.committed = 0
        self.count = count

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.committed += 1

    async def refresh(self, value):
        return None

    async def execute(self, statement):
        return SimpleNamespace(scalar_one=lambda: self.count)


def test_activity_is_staged_for_the_callers_transaction():
    db = FakeDB()
    actor_id = uuid4()
    entity_id = uuid4()

    activity = asyncio.run(
        record_activity(
            db,
            actor_id=actor_id,
            action="TASK_CREATED",
            entity_type="task",
            entity_id=entity_id,
            description="Created a task",
        )
    )

    assert activity.user_id == actor_id
    assert activity.entity_id == entity_id
    assert db.added == [activity]
    assert db.committed == 0


def test_notification_can_join_the_callers_transaction():
    db = FakeDB()
    notification = asyncio.run(
        create_notification(
            db,
            {
                "user_id": uuid4(),
                "title": "Task assigned",
                "message": "A task was assigned to you.",
                "notification_type": "TASK_ASSIGNED",
            },
            commit=False,
        )
    )

    assert db.added == [notification]
    assert db.committed == 0


def test_unread_count_is_returned_for_one_user():
    db = FakeDB(count=3)

    count = asyncio.run(count_unread_notifications(db, uuid4()))

    assert count == 3
