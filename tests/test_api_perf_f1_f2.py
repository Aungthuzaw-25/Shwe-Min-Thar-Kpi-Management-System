"""API perf F1 + F2 fixes (2026-11-06).

F1: role-scope 11 -> 3 statements, process-tracking 14 -> 2, with
response bodies byte-identical to the pre-fix baseline (golden
bodies captured from main db7bd8e on this exact seed).
F2: scoped dashboard invalidation (a task write keeps the monthly
averages cached; a KPI/other write purges all groups; a
notification write purges only the superadmin-summary group — the
only group that reads notifications) + a stampede lock in cached_json
(N concurrent misses compute once).

Harness (same method as the audit): file SQLite, tiny deterministic
seed (4 users, 2 depts, 12 tasks, 8 KPI records), statement counts
via a before_cursor_execute listener on the fixture engine. Redis is
a fake (no Redis in this VM) — the lock/purge mechanics are Redis
commands, so the fake implements them faithfully (SET NX, fnmatch
SCAN). Wall times are harness noise and are NOT asserted.
"""

import asyncio
import fnmatch
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import cache as cache_module
from app.core.cache import dashboard_groups_for_path, invalidate_dashboard_for_path
from app.core.security import get_password_hash
from app.database.base import Base
from app.database.connection import get_db
from app.main import app
from app.modules.departments.models import Department
from app.modules.kpi.models import KPIRecord, PeriodType
from app.modules.tasks.models.task import Task, TaskStatus
from app.modules.users.models import Role, User

PASSWORD = "StrongPass!123"

# Fixed UUIDs must contain a hex letter (a-f): an all-digit UUID is
# stored by SQLite's NUMERIC-affinity id columns as a REAL, losing
# precision and colliding on UNIQUE.
SA_ID = UUID("b1111111-1111-1111-1111-111111111111")
ADMIN_ID = UUID("c2222222-2222-2222-2222-222222222222")
EMP_ID = UUID("d3333333-3333-3333-3333-333333333333")
DEPT_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
DEPT_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

GOLDEN_ROLE_SCOPE = {
    "summary": {
        "total_tasks": 12,
        "assigned_tasks": 3,
        "in_progress_tasks": 3,
        "completed_tasks": 3,
        "pending_tasks": 6,
        "submitted_tasks": 3,
        "pending_approval_tasks": 3,
        "approved_tasks": 2,
        "rejected_tasks": 1,
        "overdue_tasks": 1,
        "late_tasks": 1,
        "current_kpi_score": 76.25,
        "completion_percentage": 25.0,
        "completed": 3,
        "pending": 6,
        "approved": 2,
        "rejected": 1,
        "assigned": 3,
        "in_progress": 3,
        "submitted": 3,
        "pending_approval": 3,
    }
}

GOLDEN_PROCESS_TRACKING = {
    "tracking_metrics": {
        "TOTAL_TASKS": 12,
        "DRAFT": 3,
        "PENDING_L1": 2,
        "PENDING_L0": 1,
        "PENDING_APPROVAL": 3,
        "APPROVED": 2,
        "REJECTED": 1,
        "COMPLETED": 3,
        "ASSIGNED": 3,
        "IN_PROGRESS": 3,
        "SUBMITTED": 3,
        "OVERDUE": 1,
        "LATE": 1,
        "Draft": 3,
        "Pending L1 Approval": 2,
        "Pending L0 Approval": 1,
        "Approved": 2,
        "Rejected": 1,
        "Completed": 3,
        "Assigned": 3,
        "In Progress": 3,
        "Submitted": 3,
        "Pending Approval": 3,
        "Overdue": 1,
        "Late": 1,
    },
    "recent_process_activities": [],
    "status": "ok",
}


class FakeRedis:
    """Redis stand-in with faithful SET NX + fnmatch SCAN semantics."""

    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

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
        for key in list(self.store):
            if match is None or fnmatch.fnmatch(key, match):
                yield key

    async def ping(self):
        return True


@pytest.fixture()
def world(tmp_path, monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(cache_module, "_client", fake)
    monkeypatch.setattr(cache_module, "_client_unavailable", False)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'perf12.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c))
        async with factory() as db:
            sa_role = Role(id=UUID("aa000000-0000-0000-0000-000000000001"), name="Super Admin")
            admin_role = Role(id=UUID("aa000000-0000-0000-0000-000000000002"), name="Admin")
            member_role = Role(id=UUID("aa000000-0000-0000-0000-000000000003"), name="Department User")
            db.add_all([sa_role, admin_role, member_role])
            db.add_all([
                Department(id=DEPT_A, name="Alpha"),
                Department(id=DEPT_B, name="Beta"),
            ])
            await db.flush()
            for uid, email, role in (
                (SA_ID, "sa@example.com", sa_role),
                (ADMIN_ID, "admin@example.com", admin_role),
                (EMP_ID, "emp@example.com", member_role),
            ):
                db.add(User(
                    id=uid, email=email, first_name=email.split("@")[0],
                    last_name="Test", hashed_password=get_password_hash(PASSWORD),
                    local_password_enabled=True, must_change_password=False,
                    is_active=True, role_id=role.id,
                ))
            await db.flush()
            now = datetime.now(timezone.utc)
            statuses = [
                (TaskStatus.DRAFT, None, False),
                (TaskStatus.DRAFT, now - timedelta(days=1), False),
                (TaskStatus.PENDING_L1, None, False),
                (TaskStatus.PENDING_L1, None, True),
                (TaskStatus.PENDING_L0, None, False),
                (TaskStatus.APPROVED, None, False),
                (TaskStatus.APPROVED, None, False),
                (TaskStatus.REJECTED, None, False),
                (TaskStatus.COMPLETED, None, False),
                (TaskStatus.COMPLETED, None, False),
                (TaskStatus.COMPLETED, None, False),
                (TaskStatus.DRAFT, None, False),
            ]
            for i, (status, due, late) in enumerate(statuses):
                db.add(Task(
                    id=UUID(f"e0000000-0000-0000-0000-{i:012d}"),
                    title=f"Task {i}", status=status, due_date=due,
                    is_late=late, owner_id=EMP_ID,
                ))
            for i, pct in enumerate([80, 90, 70, 60, 100, 50, 85, 75]):
                db.add(KPIRecord(
                    id=UUID(f"f0000000-0000-0000-0000-{i:012d}"),
                    user_id=EMP_ID, department_id=DEPT_A, percentage=float(pct),
                    period_type=PeriodType.MONTHLY,
                    period_start=datetime(2026, 10, 1 + i, tzinfo=timezone.utc),
                    period_end=datetime(2026, 10, 31, 23, 59, 59, tzinfo=timezone.utc),
                ))
            await db.commit()

    asyncio.run(_init())

    seen = []

    def _count(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _count)

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=False) as test_client:
        test_client.fake = fake
        test_client.seen = seen
        test_client.factory = factory
        yield test_client
    app.dependency_overrides.pop(get_db, None)
    event.remove(engine.sync_engine, "before_cursor_execute", _count)


def _login(client, email):
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _counted(client, method, path, **kwargs):
    client.seen.clear()
    resp = getattr(client, method)(path, **kwargs)
    return resp, len(client.seen)


# ---------------------------------------------------------------------------
# F1
# ---------------------------------------------------------------------------


def test_role_scope_collapsed_to_3_statements_with_identical_body(world):
    headers = _login(world, "sa@example.com")
    resp, stmts = _counted(
        world, "get", "/api/v1/superadmin/dashboard/summary/role-scope", headers=headers
    )
    assert resp.status_code == 200, resp.text
    # 1 auth trip + 1 aggregate count query + 1 KPI average.
    assert stmts == 3, f"expected 3 statements, got {stmts}"
    assert resp.json() == GOLDEN_ROLE_SCOPE


def test_process_tracking_collapsed_to_2_statements_with_identical_body(world):
    headers = _login(world, "sa@example.com")
    resp, stmts = _counted(
        world, "get", "/api/v1/superadmin/dashboard/process-tracking", headers=headers
    )
    assert resp.status_code == 200, resp.text
    # 1 auth trip + 1 aggregate count query.
    assert stmts == 2, f"expected 2 statements, got {stmts}"
    assert resp.json() == GOLDEN_PROCESS_TRACKING


def test_role_scope_admin_still_scoped_and_cheap(world):
    headers = _login(world, "admin@example.com")
    resp, stmts = _counted(
        world, "get", "/api/v1/superadmin/dashboard/summary/role-scope", headers=headers
    )
    assert resp.status_code == 200, resp.text
    # 1 auth + 1 scope query (no departments -> early return) + 1
    # aggregate count + 1 KPI average. (An admin WITH departments
    # pays one more scope query.)
    assert stmts == 4, f"expected 4 statements, got {stmts}"
    # The admin has no departments, so only their own (zero) tasks count.
    assert resp.json()["summary"]["total_tasks"] == 0


# ---------------------------------------------------------------------------
# F2 — scoped invalidation
# ---------------------------------------------------------------------------


def test_dashboard_groups_for_path():
    assert dashboard_groups_for_path("/api/v1/tasks/abc") == (
        "unified-summary",
        "executive-overview",
        "admin-summary",
        "superadmin-summary",
    )
    assert dashboard_groups_for_path("/api/v1/tasks/bulk-create") == (
        "unified-summary",
        "executive-overview",
        "admin-summary",
        "superadmin-summary",
    )
    assert dashboard_groups_for_path("/api/v1/kpi/formulas") == (
        "unified-summary",
        "executive-overview",
        "executive-monthly-averages",
        "admin-summary",
        "superadmin-summary",
    )
    assert dashboard_groups_for_path("/api/v1/notifications/xyz/read") == (
        "superadmin-summary",
    )
    # Unknown write paths stay conservative: purge everything.
    assert dashboard_groups_for_path("/api/v1/departments") == (
        "unified-summary",
        "executive-overview",
        "executive-monthly-averages",
        "admin-summary",
        "superadmin-summary",
    )


def _seed_dashboard_keys(world):
    """Populate one cached key per dashboard group, as the endpoints do."""
    for group, key in (
        ("unified-summary", "unified-summary:u1:Super Admin:period_type=MONTHLY"),
        ("executive-overview", "executive-overview:u1:Super Admin:period_id="),
        ("executive-monthly-averages", "executive-monthly-averages:u1:Super Admin:year=2026"),
        ("admin-summary", "admin-summary:u2"),
        ("superadmin-summary", "superadmin-summary"),
    ):
        world.fake.store[f"kpi:cache:dashboard:{key}"] = json.dumps({"group": group})


def test_task_write_purge_keeps_monthly_averages(world):
    _seed_dashboard_keys(world)
    deleted = asyncio.run(
        invalidate_dashboard_for_path("/api/v1/tasks/bulk-create")
    )
    assert deleted == 4
    remaining = set(world.fake.store)
    assert remaining == {"kpi:cache:dashboard:executive-monthly-averages:u1:Super Admin:year=2026"}


def test_kpi_write_purges_all_groups(world):
    _seed_dashboard_keys(world)
    deleted = asyncio.run(
        invalidate_dashboard_for_path("/api/v1/kpi/formulas")
    )
    assert deleted == 5
    assert world.fake.store == {}


def test_notification_read_purges_only_superadmin_summary(world):
    # A notification read changes the superadmin-summary group (it
    # counts/lists unread, non-deleted notifications) and nothing else:
    # exactly the superadmin key goes, the other four groups stay cached.
    _seed_dashboard_keys(world)
    deleted = asyncio.run(
        invalidate_dashboard_for_path("/api/v1/notifications/xyz/read")
    )
    assert deleted == 1
    remaining = set(world.fake.store)
    assert "kpi:cache:dashboard:superadmin-summary" not in remaining
    assert remaining == {
        "kpi:cache:dashboard:unified-summary:u1:Super Admin:period_type=MONTHLY",
        "kpi:cache:dashboard:executive-overview:u1:Super Admin:period_id=",
        "kpi:cache:dashboard:executive-monthly-averages:u1:Super Admin:year=2026",
        "kpi:cache:dashboard:admin-summary:u2",
    }


def test_notification_delete_purges_only_superadmin_summary(world):
    # Same for a delete: only the superadmin-summary group is purged.
    _seed_dashboard_keys(world)
    deleted = asyncio.run(
        invalidate_dashboard_for_path("/api/v1/notifications/xyz")
    )
    assert deleted == 1
    remaining = set(world.fake.store)
    assert "kpi:cache:dashboard:superadmin-summary" not in remaining
    assert remaining == {
        "kpi:cache:dashboard:unified-summary:u1:Super Admin:period_type=MONTHLY",
        "kpi:cache:dashboard:executive-overview:u1:Super Admin:period_id=",
        "kpi:cache:dashboard:executive-monthly-averages:u1:Super Admin:year=2026",
        "kpi:cache:dashboard:admin-summary:u2",
    }


def test_real_write_fires_scoped_purge_through_middleware(world):
    """End to end through the middleware: a real HTTP write purges via
    the scoped function (not the old whole-namespace call).

    A real task write would need the full assignment chain seeded
    (department links + position + formula categories), so the
    task-scoped survival of the monthly averages is proven at the
    purge-function level above (same function the middleware calls);
    this test proves the middleware wiring with a departments write,
    which conservatively purges every group.
    """
    headers = _login(world, "sa@example.com")
    monthly = world.get(
        "/api/v1/dashboard/executive/monthly-averages",
        params={"year": 2026},
        headers=headers,
    )
    assert monthly.status_code == 200, monthly.text
    assert any("executive-monthly-averages" in k for k in world.fake.store)
    summary = world.get("/api/v1/dashboard/summary", headers=headers)
    assert summary.status_code == 200, summary.text
    assert any("unified-summary" in k for k in world.fake.store)

    write = world.post(
        "/api/v1/departments", json={"name": "Gamma"}, headers=headers
    )
    assert write.status_code in (200, 201), write.text

    # The middleware purge runs as a background task — give it a tick.
    # Lock keys (kpi:lock:...) are NOT purged by design — they expire
    # on their own — so assert only on the cache namespace.
    import time

    for _ in range(50):
        if not any(k.startswith("kpi:cache:") for k in world.fake.store):
            break
        time.sleep(0.05)
    assert not any(
        k.startswith("kpi:cache:") for k in world.fake.store
    ), "middleware did not purge after a real write"


# ---------------------------------------------------------------------------
# F2 — stampede lock
# ---------------------------------------------------------------------------


def test_concurrent_misses_compute_once(world):
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        await asyncio.sleep(0.2)
        return {"value": 42}

    async def _run():
        results = await asyncio.gather(*[
            cache_module.cached_json("dashboard", "stampede-test", 60, factory)
            for _ in range(8)
        ])
        return results

    results = asyncio.run(_run())
    assert calls["n"] == 1, f"factory ran {calls['n']} times, expected 1"
    assert results == [{"value": 42}] * 8
    assert world.fake.store.get("kpi:cache:dashboard:stampede-test") is not None


def test_lock_failure_falls_back_to_compute(world):
    """A cache client that cannot do SET NX (old fakes, broken Redis)
    must not wait or break — the factory just runs."""
    class NoNxRedis(FakeRedis):
        async def set(self, key, value, ex=None):
            self.store[key] = value

    world.fake.__class__ = NoNxRedis
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        return {"value": 7}

    assert asyncio.run(cache_module.cached_json("dashboard", "no-nx", 60, factory)) == {"value": 7}
    assert calls["n"] == 1
