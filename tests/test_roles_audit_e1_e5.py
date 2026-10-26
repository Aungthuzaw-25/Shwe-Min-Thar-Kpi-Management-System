"""Roles audit fixes E1–E5: guard tests.

E1: batch review rejects unknown actions at validation (422) instead
    of silently skipping every permission check.
E2: the permission catalog is the source of truth — a Department User
    holding a key via override gains the route, an Admin denied the
    key loses it, even though the old role gates said the opposite.
E3: department heads (head_id or L1) may manage their own department
    — assign/review/same-dept scope — where the old "Department
    Head" role name denied every real dept head.
E4: permission READ endpoints use the same department scope as
    mutations (self-read stays allowed).
E5: HR's department.view opens department reads (process-tracking
    is the route that actually changed; list/detail/positions were
    already permission-gated and are pinned here as regressions).
"""

import asyncio
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload, sessionmaker

import app.core.cache as cache_module
from app.core import security
from app.core.dependencies import (
    enforce_user_scope,
    is_department_head,
    require_kpi_manager,
)
from app.core.exceptions import PermissionDeniedException
from app.database.connection import Base, get_db
import app.main as main_module
from app.modules.auth.models import AuthSession
from app.modules.departments.models import Department, UserDepartment
from app.modules.tasks.models.submission_batch import SubmissionBatch
from app.modules.tasks.models.task import Task
from app.modules.users.models import (
    Permission,
    Role,
    RolePermission,
    User,
    UserPermissionOverride,
)

ALL_CODES = [
    "kpi.create", "kpi.edit", "kpi.view", "kpi.delete", "kpi.calculate",
    "department.view", "department.edit", "department.create",
    "department.delete", "system.permissions.manage", "system.settings.view",
    "system.settings.edit", "system.audit.view", "users.view", "users.edit",
    "users.create", "users.delete", "users.password_reset", "report.export",
    "tasks.approve",
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
    db_file = tmp_path / "roles.db"
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
        "kpi.create", "kpi.edit", "kpi.view", "department.view",
        "department.edit", "department.create", "department.delete",
        "system.permissions.manage", "users.view", "system.settings.view",
    ])
    hr_role = make_role("HR", [
        "department.view", "system.permissions.manage", "users.view",
    ])
    dept_role = make_role("Department User", ["kpi.view", "department.view"])

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

    sa, sa_token = make_user("sa@example.com", sa_role)
    admin, admin_token = make_user("admin@example.com", admin_role)
    hr, hr_token = make_user("hr@example.com", hr_role)
    head, head_token = make_user("head@example.com", dept_role)
    l1, l1_token = make_user("l1@example.com", dept_role)
    member, member_token = make_user("member@example.com", dept_role)
    outsider, outsider_token = make_user("outsider@example.com", dept_role)
    granted, granted_token = make_user("granted@example.com", dept_role)
    denied, denied_token = make_user("denied@example.com", admin_role)

    dept_a = Department(name="Alpha", head_id=head.id)
    dept_b = Department(name="Beta")
    session.add_all([dept_a, dept_b])
    session.flush()

    def join(user, dept, level):
        session.add(UserDepartment(
            user_id=user.id, department_id=dept.id, user_level=level,
        ))

    join(admin, dept_a, "L2")
    join(hr, dept_a, "L2")
    join(head, dept_a, "L1")
    join(l1, dept_a, "L1")
    join(member, dept_a, "L2")
    join(outsider, dept_b, "L2")
    join(granted, dept_a, "L2")
    join(denied, dept_a, "L2")
    # E2 fixtures: override grant / override deny (ALL scope).
    session.add(UserPermissionOverride(
        user_id=granted.id, permission_id=perms["department.create"].id,
        allowed=True, scope="ALL",
    ))
    session.add(UserPermissionOverride(
        user_id=denied.id, permission_id=perms["department.edit"].id,
        allowed=False, scope="ALL",
    ))
    session.commit()
    ids = SimpleNamespace(
        dept_a=dept_a.id, dept_b=dept_b.id, sa=sa.id, admin=admin.id,
        hr=hr.id, head=head.id, l1=l1.id, member=member.id,
        outsider=outsider.id, granted=granted.id, denied=denied.id,
    )
    tokens = SimpleNamespace(
        sa=sa_token, admin=admin_token, hr=hr_token, head=head_token,
        l1=l1_token, member=member_token, outsider=outsider_token,
        granted=granted_token, denied=denied_token,
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


async def _load_user(factory, user_id):
    async with factory() as db:
        user = (await db.execute(
            select(User)
            .options(
                selectinload(User.role).selectinload(Role.permissions),
                selectinload(User.permission_overrides),
            )
            .where(User.id == user_id)
        )).scalars().first()
        # Detach role/permissions so lazy loads never fire after close.
        _ = (user.role.permissions, user.permission_overrides)
        db.expunge(user)
        return user


# --- E1: batch review unknown action -------------------------------------

def test_e1_unknown_batch_action_is_rejected_at_validation(world):
    response = world.client.post(
        f"/api/v1/tasks/batch/{uuid.uuid4()}/review",
        json={"action": "BOGUS"},
        headers=_headers(world.tokens.admin),
    )
    assert response.status_code == 422, response.text


def test_e1_known_batch_action_reaches_permission_gate(world):
    # APPROVED passes validation, then the Admin (no tasks.approve)
    # is denied — proving unknown values 422 while known ones run.
    response = world.client.post(
        f"/api/v1/tasks/batch/{uuid.uuid4()}/review",
        json={"action": "APPROVED"},
        headers=_headers(world.tokens.admin),
    )
    assert response.status_code == 403, response.text


# --- E2: catalog as source of truth --------------------------------------

def test_e2_override_grant_opens_department_create(world):
    response = world.client.post(
        "/api/v1/departments/",
        json={"name": "Gamma"},
        headers=_headers(world.tokens.granted),
    )
    assert response.status_code == 201, response.text


def test_e2_override_deny_closes_department_update(world):
    response = world.client.put(
        f"/api/v1/departments/{world.ids.dept_a}",
        json={"name": "Alpha Renamed"},
        headers=_headers(world.tokens.denied),
    )
    assert response.status_code == 403, response.text


# --- E3: department heads -------------------------------------------------

@pytest.mark.asyncio
async def test_e3_head_and_l1_are_department_heads(world):
    async with world.factory() as db:
        assert await is_department_head(db, world.ids.head, world.ids.dept_a)
        assert await is_department_head(db, world.ids.l1, world.ids.dept_a)
        assert not await is_department_head(db, world.ids.member, world.ids.dept_a)
        assert not await is_department_head(db, world.ids.head, world.ids.dept_b)


@pytest.mark.asyncio
async def test_e3_head_may_manage_without_the_key(world):
    head = await _load_user(world.factory, world.ids.head)
    async with world.factory() as db:
        # Head holds no kpi.create, yet qualifies by headship.
        await require_kpi_manager(db, head, world.ids.dept_a, "kpi.create")
    member = await _load_user(world.factory, world.ids.member)
    async with world.factory() as db:
        with pytest.raises(PermissionDeniedException):
            await require_kpi_manager(db, member, world.ids.dept_a, "kpi.create")


@pytest.mark.asyncio
async def test_e3_scope_head_reads_subordinate_member_does_not(world):
    head = await _load_user(world.factory, world.ids.head)
    member = await _load_user(world.factory, world.ids.member)
    async with world.factory() as db:
        await enforce_user_scope(db, head, world.ids.member)
        with pytest.raises(PermissionDeniedException):
            await enforce_user_scope(db, member, world.ids.head)


def test_e3_head_lists_subordinate_assignments_via_api(world):
    response = world.client.get(
        f"/api/v1/kpi/assignments/user/{world.ids.member}",
        headers=_headers(world.tokens.head),
    )
    assert response.status_code == 200, response.text
    response = world.client.get(
        f"/api/v1/kpi/assignments/user/{world.ids.head}",
        headers=_headers(world.tokens.member),
    )
    assert response.status_code == 403, response.text


# --- E4: permission read scope --------------------------------------------

def test_e4_reads_are_department_scoped_like_mutations(world):
    admin = _headers(world.tokens.admin)
    assert world.client.get(
        f"/api/v1/permissions/user/{world.ids.outsider}", headers=admin
    ).status_code == 403
    assert world.client.get(
        f"/api/v1/permissions/user/{world.ids.member}", headers=admin
    ).status_code == 200
    assert world.client.get(
        f"/api/v1/permissions/user/{world.ids.admin}", headers=admin
    ).status_code == 200
    assert world.client.get(
        f"/api/v1/permissions/user/{world.ids.outsider}/effective",
        headers=admin,
    ).status_code == 403
    assert world.client.get(
        f"/api/v1/permissions/user/{world.ids.outsider}",
        headers=_headers(world.tokens.sa),
    ).status_code == 200


# --- QA FAIL fix: dead "Department Head" name + trash scope --------------
# QA gate (2026-10-25) passed all 10 numbered checks but failed on
# these two pre-existing gaps in the audit's own themes; the tests
# below pin the fixes.

@pytest.mark.asyncio
async def test_qa_head_reads_subordinate_task_outside_workflow(world):
    # NOTE: GET /tasks/{id} itself passes allow_workflow_scope=True,
    # so it admitted any same-department user even before this fix
    # and cannot pin it. The QA 404 was on the default (non-workflow)
    # path, so this test calls _authorized_task directly.
    from fastapi import HTTPException

    from app.modules.tasks.router import _authorized_task

    async with world.factory() as db:
        task = Task(
            title="Subordinate task", category="Test",
            owner_id=world.ids.member,
        )
        db.add(task)
        await db.commit()
        task_id = task.id
    head = await _load_user(world.factory, world.ids.head)
    admin = await _load_user(world.factory, world.ids.admin)
    outsider = await _load_user(world.factory, world.ids.outsider)
    plain_member = await _load_user(world.factory, world.ids.granted)
    async with world.factory() as db:
        # Real dept head (Department User + head_id/L1) reads outside
        # workflow scope, where the old "Department Head" role name
        # gave 404.
        assert (await _authorized_task(db, task_id, head)).id == task_id
        # Admin of the same department still reads.
        assert (await _authorized_task(db, task_id, admin)).id == task_id
        # Other-department user still gets 404, not the task.
        with pytest.raises(HTTPException) as exc:
            await _authorized_task(db, task_id, outsider)
        assert exc.value.status_code == 404
        # The fix does NOT over-admit plain same-department members
        # outside workflow scope; under workflow scope they still read.
        with pytest.raises(HTTPException):
            await _authorized_task(db, task_id, plain_member)
        assert (
            await _authorized_task(
                db, task_id, plain_member, allow_workflow_scope=True
            )
        ).id == task_id


def test_qa_head_views_subordinate_batch_deductions(world):
    async def seed():
        async with world.factory() as db:
            batch = SubmissionBatch(submitter_id=world.ids.member)
            db.add(batch)
            await db.commit()
            return batch.id

    batch_id = asyncio.run(seed())
    url = f"/api/v1/tasks/batch/{batch_id}/deductions"
    assert world.client.get(url, headers=_headers(world.tokens.head)).status_code == 200
    assert world.client.get(url, headers=_headers(world.tokens.member)).status_code == 200
    assert world.client.get(url, headers=_headers(world.tokens.outsider)).status_code == 403


def test_qa_deleted_list_is_department_scoped(world):
    async def seed():
        async with world.factory() as db:
            dept_role_id = (await db.execute(
                select(Role.id).where(Role.name == "Department User")
            )).scalars().first()
            sa_role_id = (await db.execute(
                select(Role.id).where(Role.name == "Super Admin")
            )).scalars().first()

            async def trashed(email, role_id, dept_id, membership_deleted=False):
                user = User(
                    email=email, hashed_password="x",
                    first_name="Trash", last_name="T",
                    role_id=role_id, is_active=False, is_deleted=True,
                )
                db.add(user)
                await db.flush()
                db.add(UserDepartment(
                    user_id=user.id, department_id=dept_id,
                    user_level="L2", is_deleted=membership_deleted,
                ))
                await db.flush()
                return user.id

            ids = (
                await trashed("trash-a@example.test", dept_role_id, world.ids.dept_a),
                await trashed("trash-b@example.test", dept_role_id, world.ids.dept_b),
                await trashed("trash-sa@example.test", sa_role_id, world.ids.dept_a),
                await trashed(
                    "trash-gone@example.test", dept_role_id, world.ids.dept_a,
                    membership_deleted=True,
                ),
            )
            await db.commit()
            return ids

    trashed_a, trashed_b, trashed_sa, trashed_gone = asyncio.run(seed())

    def trash_ids(token):
        resp = world.client.get(
            "/api/v1/users/deleted/list", headers=_headers(token)
        )
        assert resp.status_code == 200, resp.text
        return {u["id"] for u in resp.json()}

    # Admin (dept A, users.view): own department's trash only.
    admin_ids = trash_ids(world.tokens.admin)
    assert str(trashed_a) in admin_ids
    assert str(trashed_b) not in admin_ids
    # SA-accounts filter kept, even for an SA in the caller's dept.
    assert str(trashed_sa) not in admin_ids
    # Deleted memberships no longer count for visibility.
    assert str(trashed_gone) not in admin_ids
    # HR stays global (D4) but still never sees SA accounts.
    hr_ids = trash_ids(world.tokens.hr)
    assert str(trashed_a) in hr_ids
    assert str(trashed_b) in hr_ids
    assert str(trashed_gone) in hr_ids
    assert str(trashed_sa) not in hr_ids
    # SA sees everything.
    sa_ids = trash_ids(world.tokens.sa)
    assert {str(trashed_a), str(trashed_b), str(trashed_sa), str(trashed_gone)} <= sa_ids


# --- E5: HR department reads -----------------------------------------------

def test_e5_hr_department_reads_open_via_department_view(world):
    hr = _headers(world.tokens.hr)
    assert world.client.get("/api/v1/departments/", headers=hr).status_code == 200
    assert world.client.get(
        f"/api/v1/departments/{world.ids.dept_a}", headers=hr
    ).status_code == 200
    assert world.client.get(
        f"/api/v1/departments/{world.ids.dept_a}/positions", headers=hr
    ).status_code == 200
    # Process-tracking is the route whose gate actually changed
    # (was SA+Admin role-only); the three above were already
    # permission-gated and are pinned as regressions.
    assert world.client.get(
        f"/api/v1/departments/{world.ids.dept_a}/process-tracking", headers=hr
    ).status_code == 200
