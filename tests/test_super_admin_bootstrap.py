from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.database.bootstrap import (
    _ensure_initial_super_admin,
    _validate_bootstrap_configuration,
)
from app.modules.users.models import User


class _Result:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def first(self):
        return self.values[0] if self.values else None

    def scalar_one_or_none(self):
        return self.values[0] if self.values else None


class _Session:
    def __init__(self, results):
        self.results = iter(results)
        self.added = []
        self.commits = 0

    async def execute(self, _statement):
        return next(self.results)

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = uuid4()

    async def commit(self):
        self.commits += 1


def test_bootstrap_configuration_requires_strong_email_and_password():
    with pytest.raises(RuntimeError, match="incomplete"):
        _validate_bootstrap_configuration("", "")

    with pytest.raises(RuntimeError, match="email is invalid"):
        _validate_bootstrap_configuration("not-an-email", "ValidPassword123!")

    with pytest.raises(RuntimeError, match="password is invalid"):
        _validate_bootstrap_configuration("admin@example.com", "weak")


@pytest.mark.asyncio
async def test_bootstrap_creates_super_admin_without_logging_password():
    role = SimpleNamespace(id=uuid4(), name="Super Admin")
    db = _Session([_Result([role]), _Result([]), _Result([])])

    created = await _ensure_initial_super_admin(
        db,
        "admin@example.com",
        "ValidPassword123!",
    )

    assert created is True
    account = next(value for value in db.added if isinstance(value, User))
    assert account.email == "admin@example.com"
    assert account.first_name == "Super"
    assert account.last_name == "Admin"
    assert account.role_id == role.id
    assert account.hashed_password != "ValidPassword123!"  # noqa: S105 - synthetic test fixture; not a production credential
    assert all("ValidPassword123!" not in repr(value) for value in db.added)
    assert db.commits == 1


@pytest.mark.asyncio
async def test_bootstrap_skips_existing_super_admin_without_changes():
    role = SimpleNamespace(id=uuid4(), name="Super Admin")
    existing = SimpleNamespace(id=uuid4())
    db = _Session([_Result([role]), _Result([existing])])

    created = await _ensure_initial_super_admin(
        db,
        "admin@example.com",
        "ValidPassword123!",
    )

    assert created is False
    assert db.added == []
    assert db.commits == 0
