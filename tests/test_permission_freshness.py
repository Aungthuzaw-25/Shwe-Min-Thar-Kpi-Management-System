"""Permission freshness: role/position/override changes must apply
to the very NEXT API call.

The backend reloads the user (role permissions + overrides) from
the database on every request, so these tests drive the REAL write
endpoints as a Super Admin and immediately re-call a permission-
gated endpoint with the SAME user token — gains must open, losses
must close (403), and the ``permissions_version`` marker exposed by
/auth/me + /permissions/me must change so the web tier can drop its
30-second context cache. A final test proves permission writes
still purge the dashboard cache namespace.
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
from app.database.connection import Base, get_db
import app.main as main_module
from app.modules.auth.models import AuthSession
from app.modules.departments.models import Department, UserDepartment
from app.modules.users.models import Permission, Role, RolePermission, User

VIEW = "department.view"


class FakeRedis:
    """Minimal async Redis stand-in (cache + version counters)."""

    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def setex(self, key, ttl, value):
        self.store[key] = value

    async def incr(self, key):
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]

    async def delete(self, *keys):
        removed = 0
        for key in keys:
            if self.store.pop(key, None) is not None:
                removed += 1
        return removed

    async def scan_iter(self, match=None, count=None):
        import fnmatch

        for key in list(self.store):
            if match is None or fnmatch.fnmatch(key, match):
                yield key

    async def ping(self):
        return True


@pytest.fixture()
def world(tmp_path, monkeypatch):
    db_file = tmp_path / "perm.db"
    sync_engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(sync_engine)
    session = sessionmaker(sync_engine)()

    perm_view = Permission(code=VIEW, description="View a department")
    perm_manage = Permission(
        code="system.permissions.manage", description="Manage permissions"
    )
    sa = Role(name="Super Admin")
    staff = Role(name="Staff")
    manager = Role(name="Manager")
    session.add_all([perm_view, perm_manage, sa, staff, manager])
    session.flush()
    session.add(RolePermission(role_id=manager.id, permission_id=perm_view.id))
    dept = Department(name="Ops")
    session.add(dept)
    session.flush()

    def make_user(email, role):
        user = User(
            email=email,
            hashed_password="x",
            first_name="T",
            last_name=email.split("@")[0],
            role_id=role.id,
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

    admin, admin_token = make_user("admin@x.com", sa)
    alice, alice_token = make_user("alice@x.com", staff)
    session.add(
        UserDepartment(user_id=alice.id, department_id=dept.id, user_level="L2")
    )
    session.commit()
    ids = SimpleNamespace(
        dept=dept.id, staff=staff.id, manager=manager.id, alice=alice.id
    )
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
    yield SimpleNamespace(
        client=client,
        fake=fake,
        ids=ids,
        admin_token=admin_token,
        alice_token=alice_token,
    )
    main_module.app.dependency_overrides.pop(get_db, None)


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _dept_status(world, token):
    return world.client.get(
        f"/api/v1/departments/{world.ids.dept}", headers=_headers(token)
    ).status_code


def _set_role_permissions(world, role_id, codes):
    response = world.client.put(
        f"/api/v1/permissions/role/{role_id}",
        json={"permission_codes": codes},
        headers=_headers(world.admin_token),
    )
    assert response.status_code == 200, response.text


def test_role_permission_gain_and_loss_apply_on_next_call(world):
    # Staff starts without department.view.
    assert _dept_status(world, world.alice_token) == 403
    # Grant it on the role -> the SAME token works on the next call.
    _set_role_permissions(world, world.ids.staff, [VIEW])
    assert _dept_status(world, world.alice_token) == 200
    # Remove it again -> the next call is closed again (real 403).
    _set_role_permissions(world, world.ids.staff, [])
    assert _dept_status(world, world.alice_token) == 403


def test_role_change_applies_on_next_call(world):
    assert _dept_status(world, world.alice_token) == 403
    response = world.client.put(
        f"/api/v1/users/{world.ids.alice}",
        json={"role_id": str(world.ids.manager)},
        headers=_headers(world.admin_token),
    )
    assert response.status_code == 200, response.text
    # Manager has department.view: next call with the same token opens.
    assert _dept_status(world, world.alice_token) == 200
    me = world.client.get("/api/v1/auth/me", headers=_headers(world.alice_token))
    assert me.json()["role"] == "Manager"
    # And moving her back closes it again.
    response = world.client.put(
        f"/api/v1/users/{world.ids.alice}",
        json={"role_id": str(world.ids.staff)},
        headers=_headers(world.admin_token),
    )
    assert response.status_code == 200, response.text
    assert _dept_status(world, world.alice_token) == 403


def test_override_deny_beats_role_and_removal_restores(world):
    _set_role_permissions(world, world.ids.staff, [VIEW])
    assert _dept_status(world, world.alice_token) == 200
    # An explicit deny override wins over the role grant, immediately.
    response = world.client.put(
        f"/api/v1/permissions/user/{world.ids.alice}",
        json={"permission_code": VIEW, "allowed": False, "scope": "ALL"},
        headers=_headers(world.admin_token),
    )
    assert response.status_code == 200, response.text
    assert _dept_status(world, world.alice_token) == 403
    # Removing the override restores the role grant on the next call.
    response = world.client.delete(
        f"/api/v1/permissions/user/{world.ids.alice}/{VIEW}",
        headers=_headers(world.admin_token),
    )
    assert response.status_code == 204, response.text
    assert _dept_status(world, world.alice_token) == 200


def test_permissions_version_changes_with_each_write_kind(world):
    def version():
        me = world.client.get(
            "/api/v1/permissions/me", headers=_headers(world.alice_token)
        )
        assert me.status_code == 200
        return me.json()["permissions_version"]

    v0 = version()
    assert v0 == "u0-r0"
    # Role permission edit bumps the role component.
    _set_role_permissions(world, world.ids.staff, [VIEW])
    v1 = version()
    assert v1 != v0 and v1.endswith("-r1")
    # A user-level write (role change) bumps the user component.
    world.client.put(
        f"/api/v1/users/{world.ids.alice}",
        json={"role_id": str(world.ids.manager)},
        headers=_headers(world.admin_token),
    )
    me = world.client.get("/api/v1/auth/me", headers=_headers(world.alice_token))
    v2 = me.json()["permissions_version"]
    assert v2.startswith("u1-") and v2 != v1
    # An override write bumps the user component again.
    world.client.put(
        f"/api/v1/permissions/user/{world.ids.alice}",
        json={"permission_code": VIEW, "allowed": False, "scope": "ALL"},
        headers=_headers(world.admin_token),
    )
    v3 = version()
    assert v3.startswith("u2-") and v3 != v2


def test_permission_write_purges_dashboard_cache(world):
    world.fake.store["kpi:cache:dashboard:superadmin-summary"] = "{}"
    world.fake.store["kpi:cache:dashboard:unified:alice"] = "{}"
    _set_role_permissions(world, world.ids.staff, [VIEW])
    assert "kpi:cache:dashboard:superadmin-summary" not in world.fake.store
    assert "kpi:cache:dashboard:unified:alice" not in world.fake.store
