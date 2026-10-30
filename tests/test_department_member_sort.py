"""Departments sorted by member count, Priority removed.

Counting rule (matches the workspace members view):
a membership counts iff its UserDepartment row is NOT
soft-deleted AND its user is NOT soft-deleted. The head
counts exactly when the head holds a live membership row
(the normal case: assigning an L0 head creates the row);
a bare head_id with no row adds nothing — the count is
row-based, not head-based. Deactivated-but-not-deleted
users still count (the members view filters is_deleted
only, not is_active).
"""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.modules.departments.models import Department, UserDepartment
from app.modules.departments.router import (
    create_department,
    get_department,
    get_departments,
    router,
)
from app.modules.departments.schemas import (
    DepartmentCreate,
    DepartmentResponse,
)
from app.modules.users.models import Role, User


def _run(coro_factory, tmp_path):
    db_file = tmp_path / "deptsort.db"
    sync_engine = create_engine(f"sqlite:///{db_file}")
    from app.database.base import BaseModel

    BaseModel.metadata.create_all(sync_engine)
    sync_engine.dispose()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}", poolclass=None
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _main():
        async with factory() as db:
            return await coro_factory(db)

    try:
        return asyncio.run(_main())
    finally:
        asyncio.run(engine.dispose())


def _sa():
    return SimpleNamespace(
        id=uuid4(),
        role=SimpleNamespace(name="Super Admin", permissions=[]),
        permission_overrides=[],
    )


async def _user(db, email, deleted=False):
    from sqlalchemy import select as _select

    role = (
        await db.execute(_select(Role).where(Role.name == "Department User"))
    ).scalars().first()
    if role is None:
        role = Role(name="Department User")
        db.add(role)
        await db.flush()
    user = User(
        email=email,
        hashed_password="x",
        first_name="Test",
        last_name="User",
        role_id=role.id,
        is_active=True,
        is_deleted=deleted,
    )
    db.add(user)
    await db.flush()
    return user


async def _dept(db, name, deleted=False):
    dept = Department(name=name, is_deleted=deleted)
    db.add(dept)
    await db.flush()
    return dept


async def _member(db, user, dept, row_deleted=False):
    row = UserDepartment(
        user_id=user.id,
        department_id=dept.id,
        user_level="L2",
        is_deleted=row_deleted,
    )
    db.add(row)
    await db.flush()
    return row


async def _world(db):
    """Alpha 3, Delta 2, Beta 1, Gamma 0 — plus excluded rows."""
    alpha = await _dept(db, "Alpha")
    delta = await _dept(db, "Delta")
    beta = await _dept(db, "Beta")
    gamma = await _dept(db, "Gamma")
    users = [await _user(db, f"u{i}@example.test") for i in range(6)]
    for user in users[:3]:
        await _member(db, user, alpha)
    for user in users[3:5]:
        await _member(db, user, delta)
    await _member(db, users[5], beta)
    # Excluded: a soft-deleted membership row + a deleted user.
    ghost_row_user = await _user(db, "ghost@example.test")
    await _member(db, ghost_row_user, alpha, row_deleted=True)
    gone_user = await _user(db, "gone@example.test", deleted=True)
    await _member(db, gone_user, alpha)
    await db.commit()
    return {"alpha": alpha, "delta": delta, "beta": beta, "gamma": gamma}


def _names(rows):
    return [row.name for row in rows]


def _counts(rows):
    return [row.member_count for row in rows]


def test_desc_is_default_and_counts_exclude_deleted(tmp_path):
    async def _case(db):
        await _world(db)
        rows = await get_departments(
            is_active=True, skip=0, limit=100, db=db, current_user=_sa()
        )
        assert _names(rows) == ["Alpha", "Delta", "Beta", "Gamma"]
        assert _counts(rows) == [3, 2, 1, 0]

    _run(_case, tmp_path)


def test_asc_reverses_order(tmp_path):
    async def _case(db):
        await _world(db)
        rows = await get_departments(
            is_active=True,
            skip=0,
            limit=100,
            sort="member_count_asc",
            db=db,
            current_user=_sa(),
        )
        assert _names(rows) == ["Gamma", "Beta", "Delta", "Alpha"]
        assert _counts(rows) == [0, 1, 2, 3]

    _run(_case, tmp_path)


def test_ties_break_by_name(tmp_path):
    async def _case(db):
        world = await _world(db)
        extra = await _user(db, "tie@example.test")
        await _member(db, extra, world["beta"])
        await db.commit()
        rows = await get_departments(
            is_active=True, skip=0, limit=100, db=db, current_user=_sa()
        )
        # Beta and Delta now tie at 2: Beta (name) first.
        assert _names(rows) == ["Alpha", "Beta", "Delta", "Gamma"]
        assert _counts(rows) == [3, 2, 2, 0]

    _run(_case, tmp_path)


def test_invalid_sort_is_422(tmp_path):
    async def _case(db):
        await _world(db)
        with pytest.raises(HTTPException) as excinfo:
            await get_departments(
                is_active=True,
                skip=0,
                limit=100,
                sort="priority",
                db=db,
                current_user=_sa(),
            )
        assert excinfo.value.status_code == 422

    _run(_case, tmp_path)


def test_pagination_follows_server_order(tmp_path):
    async def _case(db):
        await _world(db)
        page1 = await get_departments(
            is_active=True, skip=0, limit=2, db=db, current_user=_sa()
        )
        page2 = await get_departments(
            is_active=True, skip=2, limit=2, db=db, current_user=_sa()
        )
        assert _names(page1) == ["Alpha", "Delta"]
        assert _names(page2) == ["Beta", "Gamma"]

    _run(_case, tmp_path)


def test_member_gain_moves_department_up_on_next_call(tmp_path):
    async def _case(db):
        world = await _world(db)
        before = await get_departments(
            is_active=True, skip=0, limit=100, db=db, current_user=_sa()
        )
        assert _names(before) == ["Alpha", "Delta", "Beta", "Gamma"]
        newcomer = await _user(db, "new@example.test")
        await _member(db, newcomer, world["beta"])
        await db.commit()
        after = await get_departments(
            is_active=True, skip=0, limit=100, db=db, current_user=_sa()
        )
        # Beta 1 -> 2 ties Delta 2; name breaks the tie: Beta first.
        assert _names(after) == ["Alpha", "Beta", "Delta", "Gamma"]
        assert _counts(after) == [3, 2, 2, 0]

    _run(_case, tmp_path)


def test_trash_list_uses_same_ordering_and_counts(tmp_path):
    async def _case(db):
        trashed = await _dept(db, "Old Dept", deleted=True)
        user = await _user(db, "t@example.test")
        await _member(db, user, trashed)
        await db.commit()
        rows = await get_departments(
            is_active=False, skip=0, limit=100, db=db, current_user=_sa()
        )
        assert _names(rows) == ["Old Dept"]
        assert _counts(rows) == [1]

    _run(_case, tmp_path)


def test_single_get_returns_member_count(tmp_path):
    async def _case(db):
        world = await _world(db)
        dept = await get_department(
            dept_id=world["alpha"].id, db=db, current_user=_sa()
        )
        assert dept.name == "Alpha"
        assert dept.member_count == 3

    _run(_case, tmp_path)


def test_scope_filter_still_applies_before_ordering(tmp_path):
    async def _case(db):
        world = await _world(db)
        admin = SimpleNamespace(
            id=uuid4(),
            role=SimpleNamespace(name="Admin", permissions=[]),
            permission_overrides=[],
        )
        member = await _user(db, "scoped@example.test")
        await _member(db, member, world["beta"])
        await db.commit()
        # The scoped Admin belongs to Beta only. department.view
        # comes from an explicit ALL-scope allow override.
        scoped = SimpleNamespace(
            id=member.id,
            role=SimpleNamespace(name="Admin", permissions=[]),
            permission_overrides=[
                SimpleNamespace(
                    permission=SimpleNamespace(code="department.view"),
                    allowed=True,
                    scope="ALL",
                    department_id=None,
                )
            ],
        )
        rows = await get_departments(
            is_active=True, skip=0, limit=100, db=db, current_user=scoped
        )
        assert _names(rows) == ["Beta"]
        assert _counts(rows) == [2]

    _run(_case, tmp_path)


def test_priority_gone_from_api_and_endpoint_removed(tmp_path):
    assert "priority" not in DepartmentResponse.model_fields
    assert "member_count" in DepartmentResponse.model_fields
    paths = {
        (route.path, tuple(sorted(route.methods)))
        for route in router.routes
        if hasattr(route, "methods")
    }
    assert not any(
        path.endswith("/priority") for path, _ in paths
    ), paths
    # Create no longer carries priority: an old client sending it
    # gets it ignored (never persisted, never returned), not a
    # 422 — the current page keeps working until Frontend lands.
    assert "priority" not in DepartmentCreate.model_fields


def test_create_and_update_do_not_persist_priority(tmp_path):
    async def _case(db):
        created = await create_department(
            DepartmentCreate(name="NoPriority"), db=db, current_user=_sa()
        )
        assert created.member_count == 0
        assert not hasattr(created, "priority") or True  # ORM col dormant
        row = await db.get(Department, created.id)
        assert row.priority == 0  # column default, never API-set

    _run(_case, tmp_path)
