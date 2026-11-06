"""Regression: /superadmin/users 500 on the FastAPI-only (Railway) tier.

Root cause (2026-11-06, live Railway main 2d671c7): the Users page
(and Departments / Permissions / Targets / Positions) renders
`{{ permission_map | default(null) | tojson }}` in its template, but
the FastAPI route never passed `permission_map`. `null` is a Nunjucks
literal, not a Jinja2 one, so Jinja2's default(null) returned
Undefined and tojson raised "Object of type Undefined is not JSON
serializable" — a 500 on every FastAPI-tier render, while the SA
dashboard (which never tojson-encodes an absent map) stayed 200.

Two layers: (1) the templates changed `default(null)` →
`default(none)` — `none` is Jinja2's null and also evaluates to null
in Nunjucks, so the shared templates render identically on both
tiers (proven byte-identical with the repo runtime); (2) the FastAPI
routes pass the real map (pure Python over already-loaded
collections, 0 extra DB trips — same as get_employee_context and
the Reports route already do).

These tests pin the render on a real-loader world (fresh SQLite file,
SA + Manager users, no seed, Redis/MinIO genuinely unavailable):
SA gets 200 on all five pages with the real map embedded, a non-SA
is still redirected off /superadmin/users, and the Users list API
the page calls works on the same DB.
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


def _build_world(tmp_path):
    db_file = tmp_path / "userspage.db"
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
        if with_dept:
            session.add(
                UserDepartment(user_id=user.id, department_id=dept.id, user_level="L1")
            )
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

    _, sa_token = make_user("sa@example.com", sa_role, with_dept=True)
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


def test_users_page_renders_200_with_real_permission_map(world):
    resp = world.client.get(
        "/superadmin/users?view=active", headers=_headers(world.sa_token)
    )
    assert resp.status_code == 200, resp.text[:500]
    # The real map is embedded — not null, not a crash.
    assert 'viewerPermissions = {"users.view": true}' in resp.text
    assert "const viewerIsSuperAdmin = true;" in resp.text


def test_users_page_renders_200_without_query(world):
    resp = world.client.get("/superadmin/users", headers=_headers(world.sa_token))
    assert resp.status_code == 200, resp.text[:500]


@pytest.mark.parametrize(
    "url",
    [
        "/superadmin/departments",
        "/superadmin/permissions",
        "/superadmin/targets",
        "/positions",
    ],
)
def test_sibling_pages_render_200(world, url):
    # Same root cause, same fix: each template tojson-encodes the map.
    resp = world.client.get(url, headers=_headers(world.sa_token))
    assert resp.status_code == 200, resp.text[:500]
    assert "viewerPermissions = {" in resp.text


def test_users_page_redirects_non_sa(world):
    resp = world.client.get(
        "/superadmin/users?view=active",
        headers=_headers(world.mgr_token),
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303, 307)
    assert resp.headers["location"] == "/dashboard"


def test_users_list_api_works_on_same_db(world):
    resp = world.client.get(
        "/api/v1/users/?is_active=true", headers=_headers(world.sa_token)
    )
    assert resp.status_code == 200, resp.text[:500]
    emails = {row["email"] for row in resp.json()}
    assert "sa@example.com" in emails
    assert "mgr@example.com" in emails


def _minimal_context():
    """The pre-fix route context: everything the page routes pass
    EXCEPT permission_map — the exact prod condition that 500'd."""
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/superadmin/users",
        "headers": [],
        "query_string": b"",
    }
    avatar = {
        "display_name": "Boss Admin",
        "full_name": "Boss Admin",
        "name": "Boss Admin",
        "avatar_url": None,
        "initials": "BA",
        "avatar_color_index": 0,
    }
    return {
        "request": Request(scope),
        "current_user": {**avatar, "user_id": "x", "email": "sa@example.com", "role": "Super Admin"},
        "user_avatar": avatar,
        "user_email": "sa@example.com",
        "user_name": "Boss Admin",
        "user_role": "Super Admin",
        "version": "1.0.0",
    }


@pytest.mark.parametrize(
    "template,expected",
    [
        ("dashboard/users.njk", "viewerPermissions = null;"),
        ("dashboard/departments.njk", "viewerPermissions = null;"),
        ("dashboard/permissions.njk", "viewerPermissions = null;"),
        ("dashboard/positions.njk", "viewerPermissions = null;"),
        ("dashboard/target.njk", "viewerPermissions = null;"),
        ("dashboard/executive_dashboard_content.njk", "permissions: null"),
    ],
)
def test_templates_render_without_permission_map(template, expected):
    """The decided stopgap, pinned for ALL SIX templates: `default(none)`
    (Jinja2's null) must render even when no route passes permission_map —
    the pre-fix shape raised "Object of type Undefined is not JSON
    serializable" on the FastAPI/Jinja2 tier. The map-less render degrades
    to null (fail-open, buttons shown; the API still enforces every
    action). This class of bug must never ship silently again: the
    FastAPI-tier render of these templates was never tested before."""
    from app.web.template_support import templates

    html = templates.get_template(template).render(_minimal_context())
    assert expected in html
