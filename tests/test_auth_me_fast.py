"""GET /api/v1/auth/me after the re-query kill (perf fix, 2026-11-03).

The old handler re-selected the user with selectinload (a user select
plus extra position/department trips on every page load). The handler
now answers from the user get_current_user already loaded — position
and department names joinedloaded in the SAME round trip — and the two
SA flags come from one combined query instead of two sequential ones.

These tests pin both halves:
- a real-loader world (file SQLite + TestClient, the proven freshness
  pattern): /me values, shapes, and the permission map must be
  byte-identical to the old handler's, including the fragility case —
  the map must include override-only codes, which proves overrides
  (and role permissions) are really loaded for get_permission_map.
- a FakeDB route test: the handler must issue exactly ONE db.execute
  for an SA (the combined flags query) and ZERO for a non-SA — the
  old handler issued three for an SA (user select + TOTP row + SA
  count) and one for a non-SA (user select).
- Server-Timing: the timing header rides on /me (middleware).
"""

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

import app.core.cache as cache_module
from app.core import security
from app.core.permission_versions import permissions_version_for
from app.database.connection import Base, get_db
import app.main as main_module
from app.modules.auth.models import AuthSession, SATotpSecret
from app.modules.auth.router import get_current_user_context
from app.modules.departments.models import Department, UserDepartment
from app.modules.users.models import (
    Permission,
    Position,
    Role,
    RolePermission,
    User,
    UserPermissionOverride,
)

VIEW = "report.view"
EXPORT = "kpi.export"
MANAGE = "user.manage"


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def incr(self, key):
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]


def _build_world(tmp_path, monkeypatch, *, single_sa=False, totp_enrolled=False):
    db_file = tmp_path / "mefast.db"
    sync_engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(sync_engine)
    session = sessionmaker(sync_engine)()

    perm_view = Permission(code=VIEW, description="View")
    perm_export = Permission(code=EXPORT, description="Export")
    perm_manage = Permission(code=MANAGE, description="Manage")
    sa_role = Role(name="Super Admin")
    mgr_role = Role(name="Manager")
    session.add_all([perm_view, perm_export, perm_manage, sa_role, mgr_role])
    session.flush()
    for role in (sa_role, mgr_role):
        session.add(RolePermission(role_id=role.id, permission_id=perm_view.id))
        session.add(RolePermission(role_id=role.id, permission_id=perm_manage.id))
    dept = Department(name="Ops")
    session.add(dept)
    session.flush()
    position = Position(name="Lead", department_id=dept.id)
    session.add(position)
    session.flush()

    def make_user(email, role, *, with_dept=False):
        user = User(
            email=email,
            hashed_password="x",
            first_name="T",
            last_name=email.split("@")[0],
            role_id=role.id,
            position_id=position.id if with_dept else None,
            is_active=True,
        )
        session.add(user)
        session.flush()
        auth_session = AuthSession(
            user_id=user.id,
            refresh_token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            expires_at=datetime.utcnow() + timedelta(days=1),
            last_used_at=datetime.utcnow(),
        )
        session.add(auth_session)
        session.flush()
        token = security.create_access_token(
            {"sub": email, "user_id": str(user.id), "sid": str(auth_session.id)}
        )
        return user, token

    sa, sa_token = make_user("sa@example.com", sa_role, with_dept=True)
    session.add(
        UserDepartment(user_id=sa.id, department_id=dept.id, user_level="L1")
    )
    # Overrides: deny a role permission, allow an override-only code.
    session.add(
        UserPermissionOverride(
            user_id=sa.id, permission_id=perm_manage.id, allowed=False, scope="ALL"
        )
    )
    session.add(
        UserPermissionOverride(
            user_id=sa.id, permission_id=perm_export.id, allowed=True, scope="ALL"
        )
    )
    sa2_token = None
    if not single_sa:
        _, sa2_token = make_user("sa2@example.com", sa_role)
    mgr, mgr_token = make_user("mgr@example.com", mgr_role)
    session.add(
        UserPermissionOverride(
            user_id=mgr.id, permission_id=perm_manage.id, allowed=False, scope="ALL"
        )
    )
    session.add(
        UserPermissionOverride(
            user_id=mgr.id, permission_id=perm_export.id, allowed=True, scope="ALL"
        )
    )
    if totp_enrolled:
        session.add(
            SATotpSecret(
                user_id=sa.id, secret_encrypted="dummy-not-decrypted", is_confirmed=True
            )
        )
    session.commit()
    ids = SimpleNamespace(dept=dept.id, position=position.id, sa=sa.id, mgr=mgr.id)
    session.close()

    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    async def override_get_db():
        async with factory() as db_session:
            yield db_session

    main_module.app.dependency_overrides[get_db] = override_get_db
    fake = FakeRedis()
    monkeypatch.setattr(cache_module, "_client", fake)
    monkeypatch.setattr(cache_module, "_client_unavailable", False)
    client = TestClient(main_module.app, raise_server_exceptions=False)
    world = SimpleNamespace(
        client=client, fake=fake, ids=ids,
        sa_token=sa_token, sa2_token=sa2_token, mgr_token=mgr_token,
    )
    yield world
    main_module.app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def world(tmp_path, monkeypatch):
    yield from _build_world(tmp_path, monkeypatch)


@pytest.fixture()
def solo_world(tmp_path, monkeypatch):
    yield from _build_world(
        tmp_path, monkeypatch, single_sa=True, totp_enrolled=True
    )


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_me_sa_shape_through_real_loader(world):
    resp = world.client.get("/api/v1/auth/me", headers=_headers(world.sa_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["role"] == "Super Admin"
    assert body["full_name"] == "T sa"
    # No TOTP row, two SAs: setup required, no single-SA warning.
    assert body["mfa_setup_required"] is True
    assert body["single_sa_warning"] is False
    # SA bypass: every mapped code is allowed — INCLUDING the denied
    # role permission, and the override-only code must be present
    # (that presence is what proves overrides were really loaded).
    assert body["permissions"] == [EXPORT, VIEW, MANAGE]
    assert body["permissions_version"] == "u0-r0"
    assert body["department_id"] == str(world.ids.dept)
    assert body["department_name"] == "Ops"
    assert body["position_id"] == str(world.ids.position)
    assert body["position_name"] == "Lead"
    assert body["kpi_level"] == "L1"
    # Timing header rides on the call (middleware, timing only).
    assert resp.headers["Server-Timing"].startswith("app;dur=")


def test_me_single_sa_enrolled_flags(solo_world):
    resp = solo_world.client.get(
        "/api/v1/auth/me", headers=_headers(solo_world.sa_token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mfa_setup_required"] is False
    assert body["single_sa_warning"] is True


def test_me_non_sa_map_deny_beats_role(world):
    resp = world.client.get("/api/v1/auth/me", headers=_headers(world.mgr_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["role"] == "Manager"
    assert body["mfa_setup_required"] is False
    assert body["single_sa_warning"] is False
    # Deny beats the role grant (MANAGE excluded); the allow override
    # grants the override-only code; no department/position links.
    assert body["permissions"] == [EXPORT, VIEW]
    assert body["department_name"] == "No Department"
    assert body["department_id"] is None
    assert body["position_name"] == "No Position"
    assert body["kpi_level"] is None


# --- FakeDB trip pins: the handler itself must not re-query the user.


class _OneRow:
    def __init__(self, row):
        self.row = row

    def one(self):
        return self.row


class FakeDB:
    """Counts db.execute calls; answers the combined SA-flags query."""

    def __init__(self, flags_row):
        self.calls = 0
        self.flags_row = flags_row

    async def execute(self, statement):
        self.calls += 1
        return _OneRow(self.flags_row)


def _fake_user(*, sa):
    role = SimpleNamespace(
        name="Super Admin" if sa else "Manager",
        permissions=[
            SimpleNamespace(code=VIEW),
            SimpleNamespace(code=MANAGE),
        ],
    )
    link = SimpleNamespace(
        is_deleted=False,
        user_level="L1",
        department_id=uuid.uuid4(),
        department=SimpleNamespace(name="Ops"),
    )
    return SimpleNamespace(
        id=uuid.uuid4(),
        first_name="T",
        last_name="sa" if sa else "mgr",
        role=role,
        role_id=uuid.uuid4(),
        permission_overrides=[
            SimpleNamespace(
                permission=SimpleNamespace(code=MANAGE),
                allowed=False,
                scope="ALL",
                department_id=None,
            ),
            SimpleNamespace(
                permission=SimpleNamespace(code=EXPORT),
                allowed=True,
                scope="ALL",
                department_id=None,
            ),
        ],
        user_departments=[link] if sa else [],
        position_id=uuid.uuid4() if sa else None,
        position=SimpleNamespace(name="Lead") if sa else None,
    )


@pytest.mark.asyncio
async def test_me_route_issues_one_db_call_for_sa(monkeypatch):
    monkeypatch.setattr(cache_module, "_client", FakeRedis())
    monkeypatch.setattr(cache_module, "_client_unavailable", False)
    db = FakeDB((0, 2))  # 0 confirmed TOTP rows, 2 active SAs
    result = await get_current_user_context(db=db, current_user=_fake_user(sa=True))
    assert db.calls == 1  # only the combined flags query — no user re-query
    assert result["mfa_setup_required"] is True
    assert result["single_sa_warning"] is False
    assert result["permissions"] == [EXPORT, VIEW, MANAGE]
    assert result["department_name"] == "Ops"
    assert result["position_name"] == "Lead"
    assert result["permissions_version"] == "u0-r0"


@pytest.mark.asyncio
async def test_me_route_issues_zero_db_calls_for_non_sa(monkeypatch):
    monkeypatch.setattr(cache_module, "_client", FakeRedis())
    monkeypatch.setattr(cache_module, "_client_unavailable", False)
    db = FakeDB((0, 2))
    result = await get_current_user_context(db=db, current_user=_fake_user(sa=False))
    assert db.calls == 0  # SA flags short-circuit; no re-query at all
    assert result["mfa_setup_required"] is False
    assert result["single_sa_warning"] is False
    assert result["permissions"] == [EXPORT, VIEW]


@pytest.mark.asyncio
async def test_permissions_version_gathered_values(monkeypatch):
    fake = FakeRedis()
    fake.store["permver:user:u1"] = 3
    fake.store["permver:role:r1"] = 7
    monkeypatch.setattr(cache_module, "_client", fake)
    monkeypatch.setattr(cache_module, "_client_unavailable", False)
    user = SimpleNamespace(id="u1", role_id="r1")
    assert await permissions_version_for(user) == "u3-r7"
    user_no_role = SimpleNamespace(id="u1", role_id=None, role=None)
    assert await permissions_version_for(user_no_role) == "u3-r0"
