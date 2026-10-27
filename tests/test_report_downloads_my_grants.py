"""my-grants endpoint: the caller reads only their own active grants.

Contract (Stage 3 frontend already calls this path; until now it
404d and the non-SA download block stayed hidden):
- GET /api/v1/system/report-downloads/my-grants -> the caller's own
  active grants [{id, scope_type, department_id, department_name,
  granted_at}]; [] when the caller holds none (never 404).
- Revoked (is_active False) and deleted (is_deleted True) grants are
  never listed — the same active_grants query the download gate
  uses, so the list and the gate cannot disagree.
- Another user's grants never leak, and Super Admins get [] here
  (they need no grant; the SA-only list endpoint is the management
  view). No catalog permission is required (Amendment #8).
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

import app.main as main_module
from app.core.dependencies import get_current_user
from app.database.connection import Base, get_db
from app.models.report_download import ReportDownloadGrant
from app.modules.departments.models import Department
from app.modules.users.models import Role, User


def _user(role, email):
    return User(
        id=uuid.uuid4(), email=email, hashed_password="<redacted>",
        first_name="T", last_name=email.split("@")[0],
        role=role, is_active=True,
    )


def _grant(user, scope, dept=None, *, active=True, deleted=False):
    return ReportDownloadGrant(
        user_id=user.id, scope_type=scope,
        department_id=dept.id if dept else None,
        is_active=active, is_deleted=deleted,
    )


@pytest.fixture()
def world(tmp_path):
    db_file = tmp_path / "mygrants.db"
    sync_engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(sync_engine)
    session = sessionmaker(sync_engine)()
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    sa_role, staff_role = Role(name="Super Admin"), Role(name="Employee")
    alpha, beta = Department(name="Alpha"), Department(name="Beta")
    session.add_all([sa_role, staff_role, alpha, beta])
    session.flush()
    sa = _user(sa_role, "sa@x.com")
    grantee = _user(staff_role, "grantee@x.com")
    plain = _user(staff_role, "plain@x.com")
    other = _user(staff_role, "other@x.com")
    session.add_all([sa, grantee, plain, other])
    session.flush()
    session.add_all([
        _grant(grantee, "COMPANY"),
        _grant(grantee, "DEPARTMENT", alpha),
        _grant(grantee, "DEPARTMENT", beta, active=False),  # revoked: hidden
        _grant(grantee, "DEPARTMENT", alpha, deleted=True),  # deleted: hidden
        _grant(other, "COMPANY"),
    ])
    session.commit()

    actor = {"user": grantee}

    async def override_get_db():
        async with factory() as db_session:
            yield db_session

    async def override_user():
        return actor["user"]

    main_module.app.dependency_overrides[get_db] = override_get_db
    main_module.app.dependency_overrides[get_current_user] = override_user
    client = TestClient(main_module.app, raise_server_exceptions=False)
    yield SimpleNamespace(
        client=client, actor=actor, sa=sa, grantee=grantee,
        plain=plain, other=other, alpha=alpha, beta=beta,
    )
    main_module.app.dependency_overrides.pop(get_db, None)
    main_module.app.dependency_overrides.pop(get_current_user, None)
    session.close()


def _my_grants(world):
    response = world.client.get("/api/v1/system/report-downloads/my-grants")
    assert response.status_code == 200, response.text
    return response.json()


def test_my_grants_returns_own_active_grants_only(world):
    grants = _my_grants(world)
    assert len(grants) == 2
    by_scope = {g["scope_type"]: g for g in grants}
    company = by_scope["COMPANY"]
    assert company["department_id"] is None
    assert company["department_name"] is None
    dept = by_scope["DEPARTMENT"]
    assert dept["department_id"] == str(world.alpha.id)
    assert dept["department_name"] == "Alpha"
    # The revoked Beta grant and the deleted Alpha grant are hidden,
    # and the other user's COMPANY grant never leaks.
    assert all(g["department_name"] != "Beta" for g in grants)
    assert all("id" in g and "granted_at" in g for g in grants)


def test_my_grants_empty_for_plain_user_and_super_admin(world):
    world.actor["user"] = world.plain
    assert _my_grants(world) == []
    world.actor["user"] = world.sa
    assert _my_grants(world) == []


def test_my_grants_never_shows_another_users_grants(world):
    world.actor["user"] = world.other
    grants = _my_grants(world)
    assert len(grants) == 1
    assert grants[0]["scope_type"] == "COMPANY"


def test_my_grants_requires_login(world):
    main_module.app.dependency_overrides.pop(get_current_user, None)
    try:
        response = world.client.get("/api/v1/system/report-downloads/my-grants")
        assert response.status_code == 401, response.text
    finally:
        async def override_user():
            return world.actor["user"]
        main_module.app.dependency_overrides[get_current_user] = override_user
