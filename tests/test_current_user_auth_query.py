from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.core import dependencies, security


class _Result:
    def __init__(self, value):
        self.value = value

    def unique(self):
        return self

    def one_or_none(self):
        return self.value

    def one(self):
        return self.value


class _AuthDB:
    def __init__(self, *results):
        self.results = list(results)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _Result(self.results.pop(0))


def _access_request():
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/protected",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
        }
    )


def _token():
    return security.create_access_token(
        {
            "sub": "alice@example.com",
            "user_id": str(uuid4()),
            "sid": str(uuid4()),
        }
    )


def _user(**overrides):
    values = {
        "is_deleted": False,
        "is_active": True,
        "is_locked": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_active_user_authentication_uses_one_db_statement():
    user = _user()
    db = _AuthDB((user, False, True))

    result = await dependencies.get_current_user(_access_request(), _token(), db)

    assert result is user
    assert len(db.statements) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "status_code", "detail"),
    [
        (
            (_user(), True, False),
            401,
            "Token has been revoked. Please log in again.",
        ),
        (
            (_user(), False, False),
            401,
            "Session has been revoked or expired.",
        ),
        (
            (_user(is_active=False), False, True),
            401,
            "User not found or account disabled.",
        ),
        (
            (_user(is_deleted=True), False, True),
            401,
            "User not found or account disabled.",
        ),
        (
            (_user(is_locked=True), False, True),
            403,
            "Account is temporarily locked.",
        ),
    ],
)
async def test_authentication_keeps_revocation_and_account_error_precedence(
    row, status_code, detail
):
    db = _AuthDB(row)

    with pytest.raises(HTTPException) as error:
        await dependencies.get_current_user(_access_request(), _token(), db)

    assert error.value.status_code == status_code
    assert error.value.detail == detail
    assert len(db.statements) == 1


@pytest.mark.asyncio
async def test_missing_subject_checks_blacklist_and_session_before_account_error():
    db = _AuthDB(None, (False, True))

    with pytest.raises(HTTPException) as error:
        await dependencies.get_current_user(_access_request(), _token(), db)

    assert error.value.status_code == 401
    assert error.value.detail == "User not found or account disabled."
    assert len(db.statements) == 2


@pytest.mark.asyncio
async def test_missing_subject_still_prefers_blacklist_error_over_session_error():
    db = _AuthDB(None, (True, False))

    with pytest.raises(HTTPException) as error:
        await dependencies.get_current_user(_access_request(), _token(), db)

    assert error.value.status_code == 401
    assert error.value.detail == "Token has been revoked. Please log in again."
    assert len(db.statements) == 2
