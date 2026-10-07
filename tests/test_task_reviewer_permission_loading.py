import asyncio
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.connection import Base
from app.modules.departments.models import Department, UserDepartment
from app.modules.tasks.service import _authorize_reviewer
from app.modules.users.models import (
    Permission,
    Role,
    RolePermission,
    User,
    UserPermissionOverride,
)


async def _test_reviewer_scope_with_async_permission_loading(database_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    tables = [
        Department.__table__,
        Role.__table__,
        Permission.__table__,
        RolePermission.__table__,
        User.__table__,
        UserDepartment.__table__,
        UserPermissionOverride.__table__,
    ]
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: Base.metadata.create_all(
                    sync_connection,
                    tables=tables,
                )
            )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as db:
            department = Department(name=f"Reviewer scope {uuid4()}")
            permission = Permission(code="tasks.approve")
            role = Role(name=f"Reviewer role {uuid4()}", permissions=[permission])
            db.add_all([department, permission, role])
            await db.flush()

            reviewer = User(
                email=f"{uuid4()}@example.test",
                hashed_password=uuid4().hex,
                first_name="Reviewer",
                last_name="L1",
                role_id=role.id,
                is_active=True,
                is_deleted=False,
            )
            owner = User(
                email=f"{uuid4()}@example.test",
                hashed_password=uuid4().hex,
                first_name="Task",
                last_name="Owner",
                role_id=role.id,
                is_active=True,
                is_deleted=False,
            )
            db.add_all([reviewer, owner])
            await db.flush()

            owner_membership = UserDepartment(
                user_id=owner.id,
                department_id=department.id,
                user_level="L2",
                is_deleted=False,
            )
            reviewer_membership = UserDepartment(
                user_id=reviewer.id,
                department_id=department.id,
                user_level="L1",
                is_deleted=False,
            )
            db.add_all([owner_membership, reviewer_membership])
            await db.commit()

            task = SimpleNamespace(
                owner=SimpleNamespace(user_departments=[owner_membership])
            )
            await _authorize_reviewer(db, task, reviewer.id, "L1")
    finally:
        await engine.dispose()


def test_reviewer_permission_overrides_are_loaded_in_async_workflow(tmp_path):
    database_path = tmp_path / "reviewer-permission-loading.sqlite"
    asyncio.run(_test_reviewer_scope_with_async_permission_loading(database_path))
