from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.auth.router import get_current_user_accounts


class FakeScalarResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return FakeScalarResult(self.rows)


class FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.statement = None

    async def execute(self, statement):
        self.statement = statement
        return FakeResult(self.rows)


@pytest.mark.asyncio
async def test_current_user_accounts_returns_only_active_linked_methods():
    user_id = uuid4()
    linked_at = datetime(2025, 2, 3, 4, 5, 6)
    account = SimpleNamespace(
        provider="google",
        provider_email="person@gmail.com",
        linked_at=linked_at,
        created_at=None,
    )
    user = SimpleNamespace(
        id=user_id,
        email="person@example.com",
        local_password_enabled=True,
        created_at=datetime(2024, 1, 2, 3, 4, 5),
    )
    db = FakeDB([account])

    result = await get_current_user_accounts(db=db, current_user=user)

    assert result == {
        "user_email": "person@example.com",
        "local_password_enabled": True,
        "created_at": user.created_at,
        "connected_accounts": {
            "google": {
                "email": "person@gmail.com",
                "connected_at": linked_at,
            }
        },
    }
    assert db.statement is not None
