import pytest

from app.commands.create_superadmin import _validate_input
from app.database.seed import PERMISSION_CATALOG, _ensure_super_admin, _seed_permissions


class _Bind:
    class dialect:
        name = "sqlite"


class _ScalarResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values

    def scalar_one_or_none(self):
        return self.values


class _Permission:
    def __init__(self, code, description):
        self.code = code
        self.description = description


class _FakeSession:
    def __init__(self, existing):
        self.existing = existing
        self.added = []
        self.commits = 0

    def get_bind(self):
        return _Bind()

    async def execute(self, _statement):
        return _ScalarResult(self.existing)

    def add(self, permission):
        self.added.append(permission)

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_super_admin_seed_is_create_only(monkeypatch):
    monkeypatch.setenv("SEED_SUPERADMIN_EMAIL", "superadmin@example.com")
    monkeypatch.setenv("SEED_SUPERADMIN_PASSWORD", "initial-only")
    db = _FakeSession(existing=None)

    created = await _ensure_super_admin(db, "role-id")

    assert created.email == "superadmin@example.com"
    assert len(db.added) == 1
    assert db.added[0].email == "superadmin@example.com"
    first_hash = db.added[0].hashed_password

    existing_db = _FakeSession(existing=db.added[0])
    monkeypatch.delenv("SEED_SUPERADMIN_PASSWORD")
    monkeypatch.delenv("SEED_SUPER_ADMIN_PASSWORD", raising=False)
    created_again = await _ensure_super_admin(existing_db, "role-id")

    assert created_again is db.added[0]
    assert existing_db.added == []
    assert db.added[0].hashed_password == first_hash


@pytest.mark.asyncio
async def test_permission_seed_reuses_existing_rows_and_adds_only_missing_rows():
    existing = [_Permission("users.view", "Custom description")]
    db = _FakeSession(existing)

    await _seed_permissions(db)

    assert [permission.code for permission in db.added] == [
        code for code, _ in PERMISSION_CATALOG if code != "users.view"
    ]
    assert existing[0].description == "Custom description"
    assert db.commits == 1


def test_initial_super_admin_input_uses_existing_password_policy():
    email, password = _validate_input(
        "  admin@example.com ",
        "ValidPassword1!",
        "ValidPassword1!",
    )

    assert email == "admin@example.com"
    assert password == "ValidPassword1!"


@pytest.mark.parametrize(
    ("email", "password", "confirmation"),
    [
        ("", "ValidPassword1!", "ValidPassword1!"),
        ("not-an-email", "ValidPassword1!", "ValidPassword1!"),
        ("admin@example.com", "ValidPassword1!", "DifferentPassword1!"),
        ("admin@example.com", "weak", "weak"),
    ],
)
def test_initial_super_admin_input_rejects_invalid_values(email, password, confirmation):
    with pytest.raises(ValueError):
        _validate_input(email, password, confirmation)
