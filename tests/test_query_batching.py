from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.system.repo import get_recent_logs_page
from app.modules.users.permission_router import _permission_audit_name_maps


class FakeResult:
    def __init__(self, rows=None, scalar=None):
        self.rows = rows or []
        self.scalar = scalar

    def all(self):
        return self.rows

    def scalar_one(self):
        return self.scalar


class FakeDB:
    def __init__(self, results):
        self.results = iter(results)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return next(self.results)


@pytest.mark.asyncio
async def test_system_log_page_gets_rows_and_total_in_one_query():
    log = object()
    db = FakeDB([FakeResult(rows=[(log, 3)])])

    items, total = await get_recent_logs_page(db, skip=0, limit=2)

    assert items == [log]
    assert total == 3
    assert len(db.statements) == 1
    assert "OVER" in str(db.statements[0]).upper()


@pytest.mark.asyncio
async def test_system_log_page_preserves_total_for_empty_page():
    db = FakeDB([FakeResult(), FakeResult(scalar=7)])

    items, total = await get_recent_logs_page(db, skip=20, limit=2)

    assert items == []
    assert total == 7
    assert len(db.statements) == 2


@pytest.mark.asyncio
async def test_permission_audit_actor_and_target_names_share_user_query():
    user_id = uuid4()
    log = SimpleNamespace(user_id=user_id, record_id=str(user_id))
    db = FakeDB(
        [
            FakeResult(
                rows=[
                    (
                        user_id,
                        "Ada",
                        "Lovelace",
                        "ada@example.com",
                        None,
                        None,
                    )
                ]
            )
        ]
    )

    actor_map, actor_avatars, target_map, target_avatars = (
        await _permission_audit_name_maps(db, [log])
    )

    user_key = str(user_id)
    assert actor_map[user_key] == "Ada Lovelace (ada@example.com)"
    assert target_map[user_key] == actor_map[user_key]
    assert actor_avatars[user_key] == target_avatars[user_key]
    assert len(db.statements) == 1
