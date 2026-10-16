import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.connection import Base
from app.modules.auth.service import AuthService, _load_authentication_user
from app.modules.departments.models import Department, UserDepartment
from app.modules.users.models import Permission, Position, Role, RolePermission, User


class FakeResult:
    def __init__(self, value):
        self._value = value

    def unique(self):
        return self

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
    authentication = AuthService.authenticate_user(
        db, "missing@example.com", "password123"
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(authentication)

    assert exc_info.value.status_code == 401


def test_login_user_lookup_uses_one_query_and_eager_loads_response_data(tmp_path):
    async def run():
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path / 'auth-user-load.sqlite'}"
        )
        tables = [
            Department.__table__,
            Role.__table__,
            Position.__table__,
            User.__table__,
            UserDepartment.__table__,
            Permission.__table__,
            RolePermission.__table__,
        ]
        try:
            async with engine.begin() as connection:
                await connection.run_sync(
                    lambda sync_connection: Base.metadata.create_all(
                        sync_connection, tables=tables
                    )
                )
            session_factory = async_sessionmaker(engine, expire_on_commit=False)
            async with session_factory() as db:
                department = Department(name=f"Auth department {uuid4()}")
                role = Role(name=f"Auth role {uuid4()}")
                db.add_all([department, role])
                await db.flush()
                position = Position(
                    name=f"Auth position {uuid4()}",
                    department_id=department.id,
                )
                user = User(
                    email=f"{uuid4()}@example.test",
                    hashed_password="unused",
                    first_name="Auth",
                    last_name="Test",
                    role_id=role.id,
                    role=role,
                    position=position,
                )
                db.add(user)
                await db.flush()
                db.add(UserDepartment(
                    user_id=user.id,
                    department_id=department.id,
                    user_level="L2",
                ))
                await db.commit()

                query_count = 0

                def count_query(*_args):
                    nonlocal query_count
                    query_count += 1

                event.listen(engine.sync_engine, "before_cursor_execute", count_query)
                try:
                    loaded_user = await _load_authentication_user(db, user.email)
                finally:
                    event.remove(
                        engine.sync_engine, "before_cursor_execute", count_query
                    )

                assert loaded_user is not None
                assert loaded_user.role.name == role.name
                assert loaded_user.position.name == position.name
                assert loaded_user.user_departments[0].department.name == department.name
                assert query_count == 1
        finally:
            await engine.dispose()

    asyncio.run(run())
