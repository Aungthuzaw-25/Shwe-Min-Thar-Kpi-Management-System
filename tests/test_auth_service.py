import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.modules.auth.service import AuthService


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return self

    def first(self):
        return self._value


class FakeDB:
    def __init__(self, user=None):
        self.user = user
        self.committed = 0
        self.added = []

    async def execute(self, stmt):
        return FakeResult(self.user)

    async def commit(self):
        self.committed += 1

    def add(self, obj):
        self.added.append(obj)


def test_authenticate_user_returns_tokens_for_valid_credentials(monkeypatch):
    user = SimpleNamespace(
        email="alice@example.com",
        is_active=True,
        is_deleted=False,
        is_locked=False,
        lockout_until=None,
        hashed_password="hashed-password",  # noqa: S106 - synthetic test fixture; not a production credential
        failed_login_attempts=0,
        role=SimpleNamespace(name="L2"),
        position=SimpleNamespace(name="Officer"),
        user_departments=[SimpleNamespace(user_level="L2", department_id="dept-1", department=SimpleNamespace(name="Finance"))],
        must_change_password=False,
        first_name="Alice",
        last_name="Wonder",
        id="u-1",
    )
    db = FakeDB(user)

    monkeypatch.setattr("app.core.security.verify_password", lambda plain, hashed: True)
    monkeypatch.setattr("app.core.security.create_access_token", lambda data: "fake-access")
    monkeypatch.setattr("app.core.security.create_refresh_token", lambda data: "fake-refresh")

    payload = asyncio.run(AuthService.authenticate_user(db, "alice@example.com", "password123"))

    assert payload["access_token"] == "fake-access"  # noqa: S105 - synthetic test fixture; not a production credential
    assert payload["refresh_token"] == "fake-refresh"  # noqa: S105 - synthetic test fixture; not a production credential
    assert payload["user"]["email"] == "alice@example.com"
    assert payload["user"]["role"]["name"] == "L2"


def test_authenticate_user_rejects_unknown_email():
    db = FakeDB(None)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(AuthService.authenticate_user(db, "missing@example.com", "password123"))

    assert exc_info.value.status_code == 401
