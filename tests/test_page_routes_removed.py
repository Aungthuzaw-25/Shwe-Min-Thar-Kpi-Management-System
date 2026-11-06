"""Regression: FastAPI-side page routes are GONE (Jinja2 removal, 2026-11-06).

The Express/Nunjucks web tier serves every page now (through the proxy,
locally and on Railway); the FastAPI tier keeps /api/*, /health, /static
and / only. These tests pin that the removed page paths answer 404 on the
API tier — for anonymous visitors AND for a Super Admin — so a page route
can never silently come back, and so a 500 can never hide behind one
again (the users-500 bug this replaces: tests/test_users_page_render.py
rendered the same paths via FastAPI/Jinja2 and asserted 200).

Kept behaviour pinned here too: GET / still redirects to /auth/login
(the proxy serves the web tier's login page at that URL), /health still
answers, /static still serves, and the API still enforces auth.
"""

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.core import security
from app.database.connection import Base, get_db
import app.main as main_module
from app.modules.auth.models import AuthSession
from app.modules.departments.models import Department, UserDepartment
from app.modules.users.models import (
    Permission,
    Position,
    Role,
    RolePermission,
    User,
)

# Every page path the FastAPI tier used to render (app/web/dashboard_web.py
# + app/web/auth_web.py + app/modules/activity_web.py). Each one is served
# by the Express web tier now (web/src/routes/{dashboard,account,auth,
# activity}.js) — the API tier must not answer them at all.
REMOVED_PAGE_PATHS = [
    "/superadmin/dashboard",
    "/admin/dashboard",
    "/dashboard",
    "/profile",
    "/account/profile",
    "/superadmin/users",
    "/superadmin/super-admins",
    "/superadmin/departments",
    "/superadmin/permissions",
    f"/superadmin/users/{uuid.uuid4()}/profile",
    "/superadmin/kpi",
    "/superadmin/department-kpi",
    "/superadmin/employee-kpi-results",
    "/superadmin/kpi-upload",
    "/superadmin/targets",
    "/superadmin/reports",
    "/positions",
    "/kpi-periods",
    "/action-plans",
    "/whats-new",
    "/system-settings",
    "/superadmin/audit-logs",
    "/superadmin/system-logs",
    "/superadmin/health",
    "/tasks",
    "/superadmin/process-tracking",
    "/auth/login",
    "/auth/change-password",
    "/notifications",
]


def _build_world(tmp_path):
    db_file = tmp_path / "pageremoved.db"
    sync_engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(sync_engine)
    session = sessionmaker(sync_engine)()

    perm = Permission(code="users.view", description="View users")
    sa_role = Role(name="Super Admin")
    mgr_role = Role(name="Manager")
    session.add_all([perm, sa_role, mgr_role])
    session.flush()
    session.add(RolePermission(role_id=sa_role.id, permission_id=perm.id))
    dept = Department(name="Ops")
    session.add(dept)
    session.flush()
    position = Position(name="Lead", department_id=dept.id)
    session.add(position)
    session.flush()

    def make_user(email, role):
        user = User(
            email=email,
            hashed_password="x",
            first_name="T",
            last_name=email.split("@")[0],
            role_id=role.id,
            position_id=position.id,
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

    _, sa_token = make_user("sa@example.com", sa_role)
    _, mgr_token = make_user("mgr@example.com", mgr_role)
    session.commit()
    session.close()

    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    async def override_get_db():
        async with factory() as db_session:
            yield db_session

    main_module.app.dependency_overrides[get_db] = override_get_db
    client = TestClient(main_module.app, raise_server_exceptions=False)
    world = SimpleNamespace(client=client, sa_token=sa_token, mgr_token=mgr_token)
    yield world
    main_module.app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def world(tmp_path):
    yield from _build_world(tmp_path)


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize("url", REMOVED_PAGE_PATHS)
def test_removed_page_routes_return_404_unauthenticated(world, url):
    resp = world.client.get(url, follow_redirects=False)
    assert resp.status_code == 404, f"{url}: {resp.status_code} {resp.text[:200]}"


@pytest.mark.parametrize("url", REMOVED_PAGE_PATHS)
def test_removed_page_routes_return_404_for_super_admin(world, url):
    # A 404 for SA too proves the route is gone — not merely auth-gated.
    resp = world.client.get(url, headers=_headers(world.sa_token))
    assert resp.status_code == 404, f"{url}: {resp.status_code} {resp.text[:200]}"


def test_root_still_redirects_to_login(world):
    resp = world.client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 303, 307)
    assert resp.headers["location"] == "/auth/login"


def test_api_and_health_and_static_still_work(world):
    # /health answers even here: 200 with a live DB, 503 when the DB is
    # unreachable in this environment — either way the route exists.
    health = world.client.get("/health")
    assert health.status_code in (200, 503), health.text[:200]
    assert health.json()["status"] in ("healthy", "unhealthy")
    # The API still enforces auth without a token.
    me = world.client.get("/api/v1/auth/me")
    assert me.status_code == 401, me.text[:200]
    # /static still serves.
    css = world.client.get("/static/css/dashboard.css")
    assert css.status_code == 200, css.text[:200]
