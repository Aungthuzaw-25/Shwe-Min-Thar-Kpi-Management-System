"""Single-path Super Admin bootstrap: BOOTSTRAP_* only, strength
checked, must_change_password=True, production guarded.

Replaces the old SEED-based seed test, whose setup (SEED vars +
weak password) is exactly the gap this fix closes: SEED values
alone must never create a Super Admin anymore.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.future import select

import app.database.seed as seed_module
from app.core.config import settings
from app.core.security import verify_password_async
from app.database.connection import Base
from app.database.seed import (
    _ensure_super_admin,
    assert_prod_seed_allowed,
    run_seed,
)
from app.models.audit_log import AuditLog
from app.modules.users.models import Permission, Role, RolePermission, User

# Role.permissions is selectin-loaded, so the permission tables must
# exist even though these tests never grant a permission.
TABLES = [
    Role.__table__,
    User.__table__,
    AuditLog.__table__,
    Permission.__table__,
    RolePermission.__table__,
]
STRONG_PASSWORD = "Bootstrap-Strong1!"
BOOTSTRAP_EMAIL = "Admin@Example.COM"


@pytest.fixture()
def world(tmp_path, monkeypatch):
    for name in (
        "BOOTSTRAP_SUPERADMIN_EMAIL",
        "BOOTSTRAP_SUPERADMIN_PASSWORD",
        "SEED_SUPERADMIN_EMAIL",
        "SEED_SUPERADMIN_PASSWORD",
        "SEED_SUPER_ADMIN_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    for name in (
        "BOOTSTRAP_SUPERADMIN_EMAIL",
        "BOOTSTRAP_SUPERADMIN_PASSWORD",
        "SEED_SUPERADMIN_EMAIL",
        "SEED_SUPERADMIN_PASSWORD",
        "SEED_SUPER_ADMIN_PASSWORD",
    ):
        monkeypatch.setattr(settings, name, None, raising=False)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/bootstrap.db")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # Tables are created synchronously here; tests only use the
    # async factory afterwards, so no event-loop juggling is needed.
    import asyncio as _asyncio

    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda c: Base.metadata.create_all(c, tables=TABLES)
            )

    _asyncio.run(_create())
    return SimpleNamespace(engine=engine, factory=factory)


async def _prepare(world, roles=("Super Admin",)):
    async with world.factory() as db:
        for name in roles:
            db.add(Role(name=name))
        await db.commit()
        role_id = (
            await db.execute(select(Role.id).where(Role.name == "Super Admin"))
        ).scalars().first()
        return role_id


async def _users(world):
    async with world.factory() as db:
        return (await db.execute(select(User))).scalars().all()


async def _audit_rows(world):
    async with world.factory() as db:
        return (await db.execute(select(AuditLog))).scalars().all()


@pytest.mark.asyncio
async def test_seed_only_values_create_no_super_admin(world, monkeypatch):
    role_id = await _prepare(world)
    monkeypatch.setenv("SEED_SUPERADMIN_EMAIL", "seed-only@example.com")
    monkeypatch.setenv("SEED_SUPERADMIN_PASSWORD", STRONG_PASSWORD)
    async with world.factory() as db:
        assert await _ensure_super_admin(db, role_id) is False
    assert await _users(world) == []


@pytest.mark.asyncio
async def test_bootstrap_weak_password_rejected(world, monkeypatch):
    role_id = await _prepare(world)
    monkeypatch.setenv("BOOTSTRAP_SUPERADMIN_EMAIL", "boss@example.com")
    monkeypatch.setenv("BOOTSTRAP_SUPERADMIN_PASSWORD", "weak")
    async with world.factory() as db:
        with pytest.raises(RuntimeError):
            await _ensure_super_admin(db, role_id)
    assert await _users(world) == []


@pytest.mark.asyncio
async def test_bootstrap_partial_config_rejected(world, monkeypatch):
    role_id = await _prepare(world)
    monkeypatch.setenv("BOOTSTRAP_SUPERADMIN_EMAIL", "boss@example.com")
    async with world.factory() as db:
        with pytest.raises(RuntimeError):
            await _ensure_super_admin(db, role_id)
    assert await _users(world) == []


@pytest.mark.asyncio
async def test_bootstrap_creates_sa_requiring_password_change(world, monkeypatch):
    role_id = await _prepare(world)
    monkeypatch.setenv("BOOTSTRAP_SUPERADMIN_EMAIL", BOOTSTRAP_EMAIL)
    monkeypatch.setenv("BOOTSTRAP_SUPERADMIN_PASSWORD", STRONG_PASSWORD)
    async with world.factory() as db:
        assert await _ensure_super_admin(db, role_id) is True
    users = await _users(world)
    assert len(users) == 1
    assert users[0].email == "admin@example.com"
    assert users[0].must_change_password is True
    assert await verify_password_async(STRONG_PASSWORD, users[0].hashed_password)
    rows = await _audit_rows(world)
    assert len(rows) == 1
    assert rows[0].action == "BOOTSTRAP_SUPER_ADMIN"
    assert rows[0].record_id == str(users[0].id)


@pytest.mark.asyncio
async def test_existing_sa_is_never_overwritten(world, monkeypatch):
    role_id = await _prepare(world)
    monkeypatch.setenv("BOOTSTRAP_SUPERADMIN_EMAIL", "first@example.com")
    monkeypatch.setenv("BOOTSTRAP_SUPERADMIN_PASSWORD", STRONG_PASSWORD)
    async with world.factory() as db:
        assert await _ensure_super_admin(db, role_id) is True
    first_hash = (await _users(world))[0].hashed_password
    monkeypatch.setenv("BOOTSTRAP_SUPERADMIN_EMAIL", "second@example.com")
    monkeypatch.setenv("BOOTSTRAP_SUPERADMIN_PASSWORD", "Another-Strong2!")
    async with world.factory() as db:
        assert await _ensure_super_admin(db, role_id) is False
    users = await _users(world)
    assert len(users) == 1
    assert users[0].email == "first@example.com"
    assert users[0].hashed_password == first_hash


@pytest.mark.asyncio
async def test_bootstrap_email_collision_raises(world, monkeypatch):
    role_id = await _prepare(world, roles=("Super Admin", "Department User"))
    async with world.factory() as db:
        dept_role_id = (
            await db.execute(
                select(Role.id).where(Role.name == "Department User")
            )
        ).scalars().first()
        db.add(User(
            email="taken@example.com", hashed_password="x",
            first_name="Plain", last_name="User", role_id=dept_role_id,
        ))
        await db.commit()
    monkeypatch.setenv("BOOTSTRAP_SUPERADMIN_EMAIL", "taken@example.com")
    monkeypatch.setenv("BOOTSTRAP_SUPERADMIN_PASSWORD", STRONG_PASSWORD)
    async with world.factory() as db:
        with pytest.raises(RuntimeError):
            await _ensure_super_admin(db, role_id)
    users = await _users(world)
    assert len(users) == 1
    assert users[0].email == "taken@example.com"


class _FakeLockSession:
    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_prod_seed_refused_without_flag(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("ALLOW_PROD_SEED", raising=False)

    def _boom():
        raise AssertionError("database must not be touched")

    recorder = AsyncMock()
    monkeypatch.setattr(seed_module, "AsyncSessionLocal", _boom)
    monkeypatch.setattr(seed_module, "_run_seed", recorder)
    with pytest.raises(RuntimeError):
        await run_seed()
    recorder.assert_not_awaited()


@pytest.mark.asyncio
async def test_prod_seed_allowed_with_explicit_flag(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("ALLOW_PROD_SEED", "true")
    recorder = AsyncMock()
    monkeypatch.setattr(seed_module, "AsyncSessionLocal", _FakeLockSession)
    monkeypatch.setattr(seed_module, "_run_seed", recorder)
    await run_seed()
    recorder.assert_awaited_once()


@pytest.mark.asyncio
async def test_nonprod_seed_needs_no_flag(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("ALLOW_PROD_SEED", raising=False)
    recorder = AsyncMock()
    monkeypatch.setattr(seed_module, "AsyncSessionLocal", _FakeLockSession)
    monkeypatch.setattr(seed_module, "_run_seed", recorder)
    await run_seed()
    recorder.assert_awaited_once()


def test_prod_guard_helper_directly(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("ALLOW_PROD_SEED", raising=False)
    with pytest.raises(RuntimeError):
        assert_prod_seed_allowed()
    monkeypatch.setenv("ALLOW_PROD_SEED", "TRUE")
    assert_prod_seed_allowed()
