from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.database.seed import PERMISSION_CATALOG, _ensure_super_admin, _seed_permissions
from app.modules.users.router import ensure_user_deletion_allowed


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

    assert created is True
    assert len(db.added) == 1
    assert db.added[0].email == "superadmin@example.com"
    first_hash = db.added[0].hashed_password

    existing_db = _FakeSession(existing=db.added[0])
    monkeypatch.delenv("SEED_SUPERADMIN_PASSWORD")
    monkeypatch.delenv("SEED_SUPER_ADMIN_PASSWORD", raising=False)
    created_again = await _ensure_super_admin(existing_db, "role-id")

    assert created_again is False
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


@pytest.mark.parametrize(
    ("target_role", "same_user", "status_code"),
    [("Super Admin", False, 403), ("Admin", True, 400)],
)
def test_protected_user_deletion_is_rejected(target_role, same_user, status_code):
    actor = SimpleNamespace(id="actor-id", role=SimpleNamespace(name="Super Admin"))
    target = SimpleNamespace(
        id=actor.id if same_user else "target-id",
        role=SimpleNamespace(name=target_role),
    )

    with pytest.raises(HTTPException) as error:
        ensure_user_deletion_allowed(target, actor)

    assert error.value.status_code == status_code
