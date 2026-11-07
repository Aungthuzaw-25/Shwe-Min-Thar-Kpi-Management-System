"""API perf Branch 2 (F3/F5/F8/F9) — golden bodies, statement counts, rate limit.

Fixture mirrors the audit harness seed (fixed UUIDs, file SQLite, Redis
off): SA/Admin/Employee, 2 departments, Admin+Employee in Alpha,
12 tasks across all statuses, 8 KPI records (avg 76.25), 3 unread
notifications. The admin scope uses the F12 patch (str -> UUID ids;
same scope set) because SQLite cannot bind a str against a UUID
column — on PG the unpatched code works.

Goldens are the PRE-FIX bodies from main db7bd8e (same harness, same
seed); the Branch 2 code must reproduce them byte-for-byte.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.main as main_module
import app.modules.kpi.dashboard_router as dashboard_router
import app.modules.system.health as health_module
from app.core import cache as cache_module
from app.core.config import settings
from app.core.security import get_password_hash
from app.database.base import Base
from app.database.connection import get_db
from app.modules.auth import router as auth_router
from app.modules.departments.models import Department, UserDepartment
from app.modules.kpi.models import KPIRecord, PeriodType
from app.modules.notifications.models import Notification
from app.modules.tasks.models.task import Task, TaskStatus
from app.modules.users.models import Role, User

PASSWORD = "StrongPass!123"
SA_ID = UUID("b1111111-1111-1111-1111-111111111111")
ADMIN_ID = UUID("c2222222-2222-2222-2222-222222222222")
EMP_ID = UUID("d3333333-3333-3333-3333-333333333333")
DEPT_A = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
DEPT_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


GOLDEN_ADMIN = {'metrics': {'total_users': 2, 'active_users': 2, 'departments': 1, 'positions': 0, 'total_tasks': 12, 'pending_approvals': 3, 'completed_tasks': 3, 'overdue_tasks': 1, 'average_kpi': 76.25}, 'users_by_department': [{'label': 'Alpha', 'value': 2}], 'task_summary': {'total': 12, 'assigned': 3, 'pending': 6, 'approved': 2, 'completed': 3, 'rejected': 1, 'overdue': 1, 'late': 1, 'completion_percentage': 25.0}, 'kpi_by_department': [{'label': 'Alpha', 'value': 76.25}], 'category_performance': [], 'category_performance_status': 'unavailable', 'status': 'ok'}


GOLDEN_SA = {'metrics': {'total_users': 3, 'total_departments': 2, 'total_kpis': 8, 'completed_kpis': 0, 'active_alerts': 3}, 'recent_alerts': [{'name': 'emp Test', 'title': 'Alert 2', 'message': 'Message 2', 'time': '03 Nov 2026, 03:30 PM'}, {'name': 'emp Test', 'title': 'Alert 1', 'message': 'Message 1', 'time': '02 Nov 2026, 03:30 PM'}, {'name': 'emp Test', 'title': 'Alert 0', 'message': 'Message 0', 'time': '01 Nov 2026, 03:30 PM'}], 'top_departments': [{'name': 'Alpha', 'completion': 76.2, 'avg_score': 76.25, 'trend': 0.0}], 'trend': {'labels': ['2026-10-02', '2026-10-03', '2026-10-04', '2026-10-05', '2026-10-06', '2026-10-07', '2026-10-08'], 'data': [90.0, 70.0, 60.0, 100.0, 50.0, 85.0, 75.0]}}


GOLDEN_UNIFIED = {'scope': {'role': 'Super Admin', 'level': None, 'department_ids': ['aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'], 'user_count': 3}, 'period': {'period_type': 'MONTHLY', 'start': None, 'end': None}, 'scorecards': {'kpi_score': {'value': 0.0, 'status': 'available'}, 'kpi_percentage': {'value': 76.25, 'status': 'available'}, 'evaluated_kpi_records': 8, 'employee_count': 3, 'department_count': 2, 'task_completion_percentage': {'value': 25.0, 'status': 'available'}, 'pending_approvals': 3}, 'trends': {'kpi_percentage': {'labels': ['2026-10-01T00:00:00', '2026-10-02T00:00:00', '2026-10-03T00:00:00', '2026-10-04T00:00:00', '2026-10-05T00:00:00', '2026-10-06T00:00:00', '2026-10-07T00:00:00', '2026-10-08T00:00:00'], 'values': [80.0, 90.0, 70.0, 60.0, 100.0, 50.0, 85.0, 75.0], 'count': 8, 'current_value': 75.0, 'previous_value': 85.0, 'delta': -10.0, 'comparison_status': 'available'}}, 'kpi_status_distribution': [{'status': 'GOOD', 'count': 8}], 'department_comparison': [{'department_id': 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa', 'department_name': 'Alpha', 'employee_count': 2, 'kpi_score': {'value': 0.0, 'status': 'available'}, 'kpi_percentage': {'value': 76.25, 'status': 'available'}, 'evaluated_count': 8}], 'task_diagnostics': {'total': 12, 'draft': 3, 'pending': 3, 'pending_approval': 3, 'approved': 2, 'completed': 3, 'rejected': 1, 'overdue': 1, 'late': 1, 'completion_percentage': {'value': 25.0, 'status': 'available'}, 'overdue_rate': {'value': 8.33, 'status': 'available'}, 'late_rate': {'value': 8.33, 'status': 'available'}}, 'approval_diagnostics': {'pending_l1': 2, 'pending_l0': 1, 'total_pending_approval': 3, 'rejected': 1}, 'category_performance': {'status': 'unavailable', 'reason': 'category_results_not_independently_stored'}, 'alerts': [{'severity': 'warning', 'type': 'overdue_tasks', 'message': 'Tasks are overdue', 'count': 1, 'source': 'tasks', 'period': 'MONTHLY'}, {'severity': 'warning', 'type': 'late_tasks', 'message': 'Tasks were submitted late', 'count': 1, 'source': 'tasks', 'period': 'MONTHLY'}, {'severity': 'info', 'type': 'pending_approvals', 'message': 'Tasks are awaiting approval', 'count': 3, 'source': 'approvals', 'period': 'MONTHLY'}], 'activity': [], 'activity_summary': {'total': 0, 'by_action': []}, 'notifications': {'unread_count': 0, 'recent': []}, 'data_quality': {'status': 'available', 'evaluated_kpi_records': 8, 'kpi_trend': 'available', 'department_comparison': 'available', 'category_performance': {'status': 'unavailable', 'reason': 'category_results_not_independently_stored'}, 'task_data': 'available', 'comparison': 'available', 'period_notes': ['Task and approval diagnostics are lifecycle-scoped because tasks do not store KPI period boundaries.'], 'metrics_unavailable': ['category_performance', 'target_data', 'attendance_intelligence'], 'warnings': ['Category-level KPI performance is unavailable because finalized category results are not independently stored.', 'Target versus actual is unavailable because scoring-rule targets are category thresholds, not authoritative aggregate targets.', 'Attendance intelligence is unavailable because no authoritative attendance result source is integrated.']}}


GOLDEN_OVERVIEW = {'periods': [], 'selected_period': None, 'summary': {'status': 'unavailable', 'average_percentage': None, 'evaluated_employee_count': None, 'no_result_employee_count': None, 'active_employee_count': None, 'status_distribution': []}, 'departments': [], 'trend': {'points': [], 'delta': None, 'direction': 'UNAVAILABLE', 'comparison_available': False, 'tolerance': 0.0}, 'signals': [], 'attention_areas': [], 'category_intelligence': {'status': 'unavailable', 'reason': 'Finalized category results are not independently persisted.'}, 'target_intelligence': {'status': 'unavailable', 'reason': 'Authoritative target-versus-actual results are not independently persisted.'}}


GOLDEN_HEALTH = {'overall': 'OK', 'components': {'db': {'status': 'OK', 'message': 'The database is responding normally.', 'details': {'migration_head_match': None, 'db_version': None}}, 'data_integrity': {'status': 'OK', 'message': 'Saved records look complete.', 'details': {'failed_probes': [], 'skipped_probes': ['departed_records_missing_name', 'departed_tasks_missing_owner']}}, 'data_flow': {'status': 'OK', 'message': 'New work is flowing in normally.', 'details': {'last_1h': {'kpi_records': 8, 'tasks': 12, 'submission_batches': 0, 'audit_logs': 2, 'total': 22}, 'last_24h': {'kpi_records': 8, 'tasks': 12, 'submission_batches': 0, 'audit_logs': 2, 'total': 22}}}, 'security': {'status': 'OK', 'message': 'Sign-in activity looks normal.', 'details': {'failed_logins_1h': 0, 'locked_accounts': 0, 'pending_unlocks': 0, 'active_sessions': 2}}, 'user_activity': {'status': 'OK', 'message': 'People are using the system.', 'details': {'active_sessions': 2, 'logins_24h': 2, 'active_users_24h': 2}}, 'minio': {'status': 'DISABLED', 'message': 'File storage is turned off in settings.', 'details': {'bucket': 'kpi-evidence', 'probe': 'disabled'}}, 'redis': {'status': 'DISABLED', 'message': 'The cache is not configured, so the system runs without it.', 'details': {'probe': 'disabled'}}}}


def _normalize_health(payload):
    """Drop run timestamps/latencies so the health body is comparable."""
    body = json.loads(json.dumps(payload))
    body.pop("checked_at", None)
    for comp in body.get("components", {}).values():
        if isinstance(comp, dict):
            comp.pop("latency_ms", None)
    body.get("components", {}).get("user_activity", {}).get("details", {}).pop(
        "last_login_at", None
    )
    # pool depends on the engine config (PG pool_size=10; sqlite
    # StaticPool has no checkedout at all, QueuePool defaults to 5),
    # not on F8 behavior — drop it like the timestamps/latencies.
    # (QA FAIL on f2639a1: the golden pinned pool size 10, but the
    # test env runs sqlite, so the actual value is None or size 5.
    # _check_db itself is untouched — this is test-only.)
    body.get("components", {}).get("db", {}).get("details", {}).pop("pool", None)
    return body


@pytest.fixture()
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", None)
    monkeypatch.setattr(cache_module, "_client", None)
    auth_router.limiter.enabled = False
    db_file = tmp_path / "shoulds.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    seen: list[str] = []
    conns: set[int] = set()
    event.listen(engine.sync_engine, "before_cursor_execute",
                 lambda c, cu, s, p, ctx, em: seen.append(s.split("\n")[0][:70]))
    event.listen(engine.pool, "checkout",
                 lambda dbapi_conn, conn_record, conn_proxy: conns.add(id(dbapi_conn)))

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
            db.add_all([
                UserDepartment(user_id=ADMIN_ID, department_id=DEPT_A, user_level="L1"),
                UserDepartment(user_id=EMP_ID, department_id=DEPT_A, user_level="L2"),
            ])
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
            for i in range(3):
                db.add(Notification(
                    id=UUID(f"a0000000-0000-0000-0000-{i:012d}"),
                    user_id=EMP_ID, title=f"Alert {i}", message=f"Message {i}",
                    notification_type="TASK_ASSIGNED", is_read=False,
                    created_at=datetime(2026, 11, 1 + i, 9, 0, 0, tzinfo=timezone.utc),
                ))
            await db.commit()

    asyncio.run(_init())

    async def override_get_db():
        async with factory() as session:
            yield session

    # F12 harness patch: the scope helper returns str ids, which SQLite
    # cannot bind against a UUID column. Same scope set, UUID-typed.
    async def _fake_scope(db, user):
        return None if user.role.name == "Super Admin" else [DEPT_A]

    monkeypatch.setattr(
        dashboard_router, "_get_admin_scope_departments", _fake_scope
    )
    main_module.app.dependency_overrides[get_db] = override_get_db
    health_module.reset_health_cache()
    # The client MUST run as a context manager: closing it closes its
    # event loop, which lets parallel.py's module-level semaphore
    # rebind to the next test's loop. A bare (never-closed) client
    # leaves its loop alive and the next gather call raises
    # "Semaphore is bound to a different event loop".
    with TestClient(main_module.app, raise_server_exceptions=False) as client:
        def login(email):
            r = client.post(
                "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
            )
            assert r.status_code == 200, r.text
            return {"Authorization": f"Bearer {r.json()['access_token']}"}

        sa = login("sa@example.com")
        admin = login("admin@example.com")
        yield type("W", (), {
            "client": client, "sa": sa, "admin": admin,
            "seen": seen, "conns": conns, "login": staticmethod(login),
        })()
    main_module.app.dependency_overrides.pop(get_db, None)
    health_module.reset_health_cache()
    auth_router.limiter.enabled = True


def _get(world, path, headers):
    world.seen.clear()
    world.conns.clear()
    resp = world.client.get(path, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json(), len(world.seen)


# ----------------------------------------------------------------------
# F3 — legacy summaries: same statements, same bodies, parallel now
# ----------------------------------------------------------------------

def test_admin_summary_golden_body_and_count(world):
    body, count = _get(world, "/api/v1/admin/dashboard/summary", world.admin)
    assert count == 7, world.seen
    assert body == GOLDEN_ADMIN


def test_sa_summary_golden_body_and_count(world):
    body, count = _get(world, "/api/v1/superadmin/dashboard/summary", world.sa)
    assert count == 5, world.seen
    assert body == GOLDEN_SA


# ----------------------------------------------------------------------
# F8 — health: same statements, same payload, 10s cache intact
# ----------------------------------------------------------------------

def test_health_golden_body_and_count(world):
    body, count = _get(world, "/api/v1/system/health", world.sa)
    assert count == 20, world.seen
    assert _normalize_health(body) == GOLDEN_HEALTH


def test_health_10s_cache_still_holds(world):
    first, _ = _get(world, "/api/v1/system/health", world.sa)
    second, count = _get(world, "/api/v1/system/health", world.sa)
    # Second call inside the 10s window is served from the in-process
    # cache: the auth trip runs, but no component query does.
    assert count == 1, world.seen
    assert _normalize_health(second) == _normalize_health(first)


# ----------------------------------------------------------------------
# F5 — unified summary: 3 KPI statements become 1 UNION ALL, same body
# ----------------------------------------------------------------------

def test_unified_summary_golden_body_and_count(world):
    body, count = _get(world, "/api/v1/dashboard/summary", world.sa)
    assert count == 14, world.seen
    assert body == GOLDEN_UNIFIED


def test_executive_overview_unchanged(world):
    body, count = _get(
        world, "/api/v1/dashboard/executive/overview", world.sa
    )
    assert count == 4, world.seen
    assert body == GOLDEN_OVERVIEW


# ----------------------------------------------------------------------
# F9 — 120/minute per token on the expensive endpoints
# ----------------------------------------------------------------------

def test_rate_limit_429_after_120_calls(world):
    # The overview is the cheapest limited endpoint; the bucket is per
    # token per endpoint (slowapi scopes by endpoint function name).
    token = world.login("sa@example.com")
    path = "/api/v1/dashboard/executive/overview"
    for _ in range(120):
        resp = world.client.get(path, headers=token)
        assert resp.status_code == 200, resp.text
    resp = world.client.get(path, headers=token)
    assert resp.status_code == 429, resp.text
    # A different token has its own bucket and is unaffected.
    other = world.login("admin@example.com")
    resp = world.client.get(path, headers=other)
    assert resp.status_code == 200, resp.text


# ----------------------------------------------------------------------
# F7 — pool sessions per summary miss stay within the semaphore cap
# ----------------------------------------------------------------------

def test_f7_sessions_per_summary_miss(world):
    _, count = _get(world, "/api/v1/dashboard/summary", world.sa)
    assert count == 14, world.seen
    # Sessions follow parallel jobs, capped by the 12-slot semaphore
    # (pool is 20); the measured harness number is ~9-10.
    assert 1 <= len(world.conns) <= 12, world.conns
