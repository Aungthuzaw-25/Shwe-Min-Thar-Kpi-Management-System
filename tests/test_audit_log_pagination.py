from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.kpi.router import get_system_audit_logs


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeDB:
    def __init__(self):
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return FakeResult([])


@pytest.mark.asyncio
async def test_audit_log_endpoint_supports_offset_search_and_action_filter():
    db = FakeDB()
    current_user = SimpleNamespace(
        role=SimpleNamespace(name="Super Admin"),
        id=uuid4(),
    )

    result = await get_system_audit_logs(
        limit=25,
        offset=50,
        search="login",
        action="LOGIN",
        db=db,
        current_user=current_user,
    )

    sql = str(db.statement)
    assert result == []
    assert "LIMIT" in sql
    assert "OFFSET" in sql
    assert "audit_logs.action =" in sql
    assert "audit_logs.old_value" in sql
