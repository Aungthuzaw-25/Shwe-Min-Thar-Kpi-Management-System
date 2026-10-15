from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.connection import Base
from app.modules.departments.models import Department, UserDepartment
from app.modules.system.repo import get_recent_logs_page
from app.modules.users.models import Position, Role, User
from app.modules.users.permission_router import _permission_audit_name_maps
from app.web import dashboard_web


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


@pytest.mark.asyncio
async def test_employee_template_context_uses_one_context_query(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'employee-context.sqlite'}")
    tables = [
        Department.__table__,
        Role.__table__,
        Position.__table__,
        User.__table__,
        UserDepartment.__table__,
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
            department = Department(name=f"Context department {uuid4()}")
            role = Role(name=f"Context role {uuid4()}")
            db.add_all([department, role])
            await db.flush()
            position = Position(
                name=f"Context position {uuid4()}",
                department_id=department.id,
            )
            user = User(
                email=f"{uuid4()}@example.test",
                hashed_password="unused",  # noqa: S106 - synthetic test fixture
                first_name="Context",
                last_name="User",
                role_id=role.id,
                role=role,
                position=position,
            )
            db.add(user)
            await db.flush()
            db.add(
                UserDepartment(
                    user_id=user.id,
                    department_id=department.id,
                    user_level="L1",
                    is_deleted=False,
                )
            )
            await db.commit()
            monkeypatch.setattr(dashboard_web, "get_permission_map", lambda _user: {})

            query_count = 0

            def count_query(*_args):
                nonlocal query_count
                query_count += 1

            event.listen(engine.sync_engine, "before_cursor_execute", count_query)
            try:
                context = await dashboard_web.get_employee_context(db, user)
            finally:
                event.remove(
                    engine.sync_engine, "before_cursor_execute", count_query
                )

            assert context["user_level"] == "L1"
            assert context["department_name"] == department.name
            assert context["position_name"] == position.name
            assert query_count == 1
    finally:
        await engine.dispose()
