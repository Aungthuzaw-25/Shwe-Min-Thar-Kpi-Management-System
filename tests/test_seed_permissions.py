import pytest

from app.database.seed import PERMISSION_CATALOG, _seed_permissions


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
async def test_permission_seed_reuses_existing_rows_and_adds_only_missing_rows():
    existing = [_Permission("users.view", "Custom description")]
    db = _FakeSession(existing)

    await _seed_permissions(db)

    assert [permission.code for permission in db.added] == [
        code for code, _ in PERMISSION_CATALOG if code != "users.view"
    ]
    assert existing[0].description == "Custom description"
    assert db.commits == 1
