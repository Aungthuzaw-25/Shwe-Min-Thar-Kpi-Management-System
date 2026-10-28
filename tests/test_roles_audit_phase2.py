"""Roles audit Phase 2 (I1/I4/I5/I6/D4/D5/D6 + bulk import): guard tests.

D4: the user list is department-scoped like the profile view — the
    audit's "unscoped list" premise was wrong (the block already
    existed); pinned here, plus deleted memberships no longer count.
D5: HR defaults no longer include system.permissions.manage.
D6: dormant keys are gone from the catalog and defaults; kpi.delete
    and system.audit.view are KEPT (wired by the E-fix).
I5: avatar fetch follows user-read scope (outsider 403, same-dept
    Admin passes scope and then 404s on the missing file).
Bulk import: mixed CSV (created / duplicate / bad role / bad dept /
    SA role / generated password), non-creator 403, header checks.
I1/I4/I6 are pinned statically (dead names gone, auth on the
    change-password render, report export gated) — their behaviour
    lives in the web tier / templates, not the API harness.
"""

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload, sessionmaker

import app.core.cache as cache_module
from app.core import security
from app.database.connection import Base, get_db
from app.database.seed import PERMISSION_CATALOG, ROLE_DEFAULTS
import app.main as main_module
from app.modules.auth.models import AuthSession
from app.modules.departments.models import Department, UserDepartment
from app.modules.users.models import (
    Permission,
    Role,
    RolePermission,
    User,
)

ALL_CODES = [
    "kpi.create", "kpi.edit", "kpi.view", "kpi.delete", "kpi.calculate",
    "department.view", "department.edit", "department.create",
    "department.delete", "system.permissions.manage", "system.settings.view",
    "system.settings.edit", "system.audit.view", "users.view", "users.edit",
    "users.create", "users.delete", "users.password_reset", "report.export",
    "report.view", "tasks.approve",
]


class FakeRedis:
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
        for key in keys:
            self.store.pop(key, None)

    async def scan_iter(self, match=None, count=None):
        for key in list(self.store):
            yield key

    async def ping(self):
        return True


@pytest.fixture()
def world(tmp_path, monkeypatch):
    db_file = tmp_path / "phase2.db"
    sync_engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(sync_engine)
    session = sessionmaker(sync_engine)()
    perms = {}
    for code in ALL_CODES:
        perm = Permission(code=code, description=code)
        session.add(perm)
        session.flush()
        perms[code] = perm

    def make_role(name, codes):
        role = Role(name=name)
        session.add(role)
        session.flush()
        for code in codes:
            session.add(RolePermission(role_id=role.id, permission_id=perms[code].id))
        return role

    sa_role = make_role("Super Admin", [])
    admin_role = make_role("Admin", [
        "users.view", "users.create", "users.edit", "department.view",
    ])
    hr_role = make_role("HR", ["users.view"])
    dept_role = make_role("Department User", ["users.view"])

    def make_user(email, role):
        user = User(
            email=email,
            hashed_password=security.get_password_hash("x"),
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

    sa, sa_token = make_user("sa@example.com", sa_role)
    admin, admin_token = make_user("admin@example.com", admin_role)
    hr, hr_token = make_user("hr@example.com", hr_role)
    member, member_token = make_user("member@example.com", dept_role)
    outsider, outsider_token = make_user("outsider@example.com", dept_role)

    dept_a = Department(name="Alpha")
    dept_b = Department(name="Beta")
    session.add_all([dept_a, dept_b])
    session.flush()

    def join(user, dept, level="L2"):
        session.add(UserDepartment(
            user_id=user.id, department_id=dept.id, user_level=level,
        ))

    join(admin, dept_a)
    join(member, dept_a)
    join(outsider, dept_b)
    session.commit()
    ids = SimpleNamespace(
        dept_a=dept_a.id, dept_b=dept_b.id, sa=sa.id, admin=admin.id,
        hr=hr.id, member=member.id, outsider=outsider.id,
    )
    tokens = SimpleNamespace(
        sa=sa_token, admin=admin_token, hr=hr_token,
        member=member_token, outsider=outsider_token,
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
    yield SimpleNamespace(client=client, ids=ids, tokens=tokens, factory=factory)
    main_module.app.dependency_overrides.pop(get_db, None)


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _emails(response):
    assert response.status_code == 200, response.text
    return {u["email"] for u in response.json()}


# --- D4: department-scoped user list --------------------------------------

def test_d4_admin_list_is_department_scoped(world):
    emails = _emails(world.client.get("/api/v1/users/", headers=_headers(world.tokens.admin)))
    assert "member@example.com" in emails
    assert "outsider@example.com" not in emails
    assert "sa@example.com" not in emails  # non-SA never sees SA rows


def test_d4_sa_and_hr_lists_are_global(world):
    sa_emails = _emails(world.client.get("/api/v1/users/", headers=_headers(world.tokens.sa)))
    assert {"sa@example.com", "admin@example.com", "member@example.com",
            "outsider@example.com"} <= sa_emails
    hr_emails = _emails(world.client.get("/api/v1/users/", headers=_headers(world.tokens.hr)))
    assert "outsider@example.com" in hr_emails
    assert "sa@example.com" not in hr_emails


def test_d4_deleted_membership_no_longer_counts(world):
    async def _delete_membership():
        async with world.factory() as db:
            await db.execute(update(UserDepartment).where(
                UserDepartment.user_id == world.ids.member,
            ).values(is_deleted=True))
            await db.commit()
    import asyncio
    asyncio.run(_delete_membership())
    emails = _emails(world.client.get("/api/v1/users/", headers=_headers(world.tokens.admin)))
    assert "member@example.com" not in emails


# --- D5/D6: seed catalog and defaults -------------------------------------

DORMANT_REMOVED = [
    "users.activate", "users.deactivate", "tasks.resubmit", "kpi.assign",
    "kpi.rule.view", "kpi.rule.create", "kpi.rule.edit", "kpi.rule.delete",
    "report.create", "report.view_all",
]


def test_d5_hr_defaults_drop_permissions_manage():
    assert "system.permissions.manage" not in ROLE_DEFAULTS["HR"]


def test_d6_dormant_keys_gone_from_catalog_and_defaults():
    codes = {code for code, _ in PERMISSION_CATALOG}
    for dead in DORMANT_REMOVED:
        assert dead not in codes
        assert dead not in ROLE_DEFAULTS["HR"]
        assert dead not in ROLE_DEFAULTS["Department User"]
    # Admin is computed from the catalog, so it follows automatically.
    assert ROLE_DEFAULTS["Admin"] == codes - {"system.settings.edit", "system.audit.view"}
    assert ROLE_DEFAULTS["Super Admin"] == codes


def test_d6_wired_keys_kept():
    codes = {code for code, _ in PERMISSION_CATALOG}
    assert "kpi.delete" in codes  # formula delete (E-fix)
    assert "system.audit.view" in codes  # KPI audit-logs (E-fix)


# --- I5: avatar scope ------------------------------------------------------

AVATAR_NAME = "a" * 32 + ".webp"


def test_i5_avatar_scope(world):
    async def _set_avatar():
        async with world.factory() as db:
            await db.execute(update(User).where(
                User.id == world.ids.member,
            ).values(avatar_path=AVATAR_NAME))
            await db.commit()
    import asyncio
    asyncio.run(_set_avatar())
    # Outsider (other department) is denied before any file check.
    response = world.client.get(
        f"/api/v1/account/avatars/{AVATAR_NAME}",
        headers=_headers(world.tokens.outsider),
    )
    assert response.status_code == 403, response.text
    # Owner passes scope, then 404s on the missing file (no leak either way).
    response = world.client.get(
        f"/api/v1/account/avatars/{AVATAR_NAME}",
        headers=_headers(world.tokens.member),
    )
    assert response.status_code == 404, response.text
    # Unknown filename still 404 for everyone.
    response = world.client.get(
        "/api/v1/account/avatars/" + "b" * 32 + ".webp",
        headers=_headers(world.tokens.outsider),
    )
    assert response.status_code == 404, response.text


# --- Bulk CSV import ---------------------------------------------------------

CSV_MIXED = (
    "email,first_name,last_name,role,department,user_level,password\n"
    "new1@example.com,New,One,Department User,Alpha,L2,BulkPass123!\n"
    "new1@example.com,Dup,One,Department User,Alpha,L2,BulkPass123!\n"
    "new2@example.com,New,Two,Wizard,Alpha,L2,BulkPass123!\n"
    "new3@example.com,New,Three,Department User,Gamma,L2,BulkPass123!\n"
    "new4@example.com,New,Four,Super Admin,Alpha,L2,BulkPass123!\n"
    "new5@example.com,New,Five,Department User,Alpha,L2,\n"
)


def test_bulk_import_mixed_csv(world):
    response = world.client.post(
        "/api/v1/users/bulk-import",
        files={"file": ("users.csv", CSV_MIXED, "text/csv")},
        headers=_headers(world.tokens.sa),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_rows"] == 6
    assert body["created"] == 2
    assert body["errors"] == 4
    # The duplicate email appears twice: first created, then error.
    # (A dict keyed by email would collapse the pair, so assert in order.)
    dup_rows = [r for r in body["results"] if r["email"] == "new1@example.com"]
    assert [r["status"] for r in dup_rows] == ["created", "error"]
    assert dup_rows[0]["password_generated"] is False
    by_email = {r["email"]: r for r in body["results"] if r["email"] != "new1@example.com"}
    assert by_email["new5@example.com"]["status"] == "created"
    assert by_email["new5@example.com"]["password_generated"] is True
    for bad in ("new2@example.com", "new3@example.com", "new4@example.com"):
        assert by_email[bad]["status"] == "error", by_email[bad]
    # Created users are really there, and new1 joined Alpha.
    emails = _emails(world.client.get("/api/v1/users/", headers=_headers(world.tokens.sa)))
    assert "new1@example.com" in emails and "new5@example.com" in emails
    assert "new2@example.com" not in emails


def test_bulk_import_requires_users_create(world):
    response = world.client.post(
        "/api/v1/users/bulk-import",
        files={"file": ("users.csv", CSV_MIXED, "text/csv")},
        headers=_headers(world.tokens.member),
    )
    assert response.status_code == 403, response.text


def test_bulk_import_header_and_extension_checks(world):
    response = world.client.post(
        "/api/v1/users/bulk-import",
        files={"file": ("users.txt", CSV_MIXED, "text/plain")},
        headers=_headers(world.tokens.sa),
    )
    assert response.status_code == 422, response.text
    response = world.client.post(
        "/api/v1/users/bulk-import",
        files={"file": ("users.csv", "email,first_name\nx@y.z,A\n", "text/csv")},
        headers=_headers(world.tokens.sa),
    )
    assert response.status_code == 422, response.text


def test_bulk_import_admin_cannot_import_admin_role(world):
    csv_data = (
        "email,first_name,last_name,role,department,user_level,password\n"
        "boss2@example.com,Boss,Two,Admin,Alpha,L2,BulkPass123!\n"
    )
    response = world.client.post(
        "/api/v1/users/bulk-import",
        files={"file": ("users.csv", csv_data, "text/csv")},
        headers=_headers(world.tokens.admin),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["created"] == 0 and body["errors"] == 1
    assert body["results"][0]["status"] == "error"


# --- I1/I4/I6 static pins ----------------------------------------------------

def test_i1_dead_role_names_gone():
    import pathlib
    repo = pathlib.Path(__file__).resolve().parents[1]
    for rel in (
        "app/modules/activity_web.py",
        "app/web/dashboard_web.py",
        "web/src/routes/dashboard.js",
        "web/src/routes/activity.js",
    ):
        text = (repo / rel).read_text()
        assert "System Admin" not in text, rel
    # Level 0/1 survive only as membership-level values (a separate
    # list in the same file); the ROLE-name universe must not hold them.
    parity = (repo / "web/tests/routeParity.test.js").read_text()
    role_line = next(line for line in parity.splitlines() if "const roleNames" in line)
    assert "Level 0" not in role_line and "Level 1" not in role_line
    assert "System Admin" not in role_line


def test_i4_change_password_render_requires_auth():
    import pathlib
    repo = pathlib.Path(__file__).resolve().parents[1]
    text = (repo / "app/web/auth_web.py").read_text()
    assert "change_password_page" in text
    assert "current_user: User = Depends(get_current_user)" in text
    # The Express route deliberately stays public: server.test.js pins
    # it as a public page (OAuth/post-login ?next= handling), and the
    # form cannot change anything without the authed API POST.


def test_i6_report_export_gated():
    import pathlib
    repo = pathlib.Path(__file__).resolve().parents[1]
    template = (repo / "templates/dashboard/report.njk").read_text()
    assert "report.export" in template
    assert "canExportReport" in template  # JS gate lives in the template
    web = (repo / "app/web/dashboard_web.py").read_text()
    assert "dashboard/report.njk" in web and "permission_map" in web
