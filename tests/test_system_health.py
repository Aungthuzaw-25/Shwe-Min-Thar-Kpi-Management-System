"""System Health endpoint (Feature Stage 2 — Backend).

Covers the SA-only gate, all seven components, the threshold
rules, the 10-second cache, and status-change-only HEALTH_CHECK
logging (no log spam while a component stays broken, one
recovery log when it heals).
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.core.config import settings
from app.core.dependencies import get_current_user
from app.database.base import BaseModel
from app.database.connection import get_db
from app.models.audit_log import AuditLog
from app.models.system import LogLevel
from app.modules.auth.models import AuthSession, LoginHistory
from app.modules.departments.models import Department, UserDepartment
from app.modules.kpi.models import KPIPeriod, KPIPeriodStatus, KPIRecord, PeriodType
from app.modules.system import health as health_module
from app.modules.tasks.models.task import Task
from app.modules.users.models import Position, Role, User
from app.services.system_log_messages import plain_message_for


def _user(role, email):
    return User(
        email=email, hashed_password="x", first_name="Test", last_name="User",
        role_id=role.id, role=role, is_active=True, is_deleted=False,
    )


@pytest.fixture()
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", None)
    db_file = tmp_path / "health.db"
    sync_engine = create_engine(f"sqlite:///{db_file}")
    BaseModel.metadata.create_all(sync_engine)
    session = sessionmaker(sync_engine)()
    async_engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    sa_role = Role(name="Super Admin")
    admin_role = Role(name="Admin")
    staff_role = Role(name="Employee")
    sales = Department(name="Sales")
    session.add_all([sa_role, admin_role, staff_role, sales])
    session.flush()
    position = Position(name="Officer", department_id=sales.id)
    sa = _user(sa_role, "sa@x.com")
    admin = _user(admin_role, "admin@x.com")
    emp = _user(staff_role, "emp@x.com")
    emp.position = position
    emp.user_departments = [
        UserDepartment(department_id=sales.id, user_level="L2", is_deleted=False)
    ]
    session.add_all([position, sa, admin, emp])
    session.commit()

    actor = {"user": sa}

    async def override_get_db():
        async with factory() as db:
            yield db

    async def override_user():
        return actor["user"]

    main_module.app.dependency_overrides[get_db] = override_get_db
    main_module.app.dependency_overrides[get_current_user] = override_user
    client = TestClient(main_module.app, raise_server_exceptions=False)
    health_module.reset_health_cache()
    health_module.reset_health_log_state()
    yield type("W", (), {
        "session": session, "factory": factory, "client": client,
        "actor": actor, "sa": sa, "admin": admin, "emp": emp,
        "sales": sales, "position": position,
    })()
    main_module.app.dependency_overrides.pop(get_db, None)
    main_module.app.dependency_overrides.pop(get_current_user, None)
    health_module.reset_health_cache()
    health_module.reset_health_log_state()
    session.close()


def _login(session, user, success=True, when=None):
    session.add(LoginHistory(
        user_id=user.id, is_success=success,
        attempted_at=when or datetime.now(UTC).replace(tzinfo=None),
    ))
    session.commit()


def _get(world):
    response = world.client.get("/api/v1/system/health")
    assert response.status_code == 200, response.text
    return response.json()


def _components(world):
    return _get(world)["components"]


# ----------------------------------------------------------------------
# Gate + happy path
# ----------------------------------------------------------------------

def test_health_requires_super_admin(world):
    world.actor["user"] = world.admin
    assert world.client.get("/api/v1/system/health").status_code == 403
    world.actor["user"] = world.emp
    assert world.client.get("/api/v1/system/health").status_code == 403
    world.actor["user"] = world.sa
    assert world.client.get("/api/v1/system/health").status_code == 200


def test_health_all_ok_and_disabled_excluded(world):
    _login(world.session, world.emp, success=True)
    world.session.add(KPIRecord(
        user_id=world.emp.id, department_id=world.sales.id,
        period_type=PeriodType.MONTHLY,
        period_start=datetime.now(UTC).replace(tzinfo=None),
        period_end=datetime.now(UTC).replace(tzinfo=None),
    ))
    world.session.add(AuthSession(
        user_id=world.emp.id, refresh_token_hash="h" * 64,
        expires_at=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1),
        last_used_at=datetime.now(UTC).replace(tzinfo=None),
    ))
    world.session.commit()

    body = _get(world)
    assert body["overall"] == "OK"
    assert set(body["components"]) == {
        "db", "data_integrity", "data_flow", "security",
        "user_activity", "minio", "redis",
    }
    assert body["components"]["db"]["status"] == "OK"
    assert body["components"]["data_integrity"]["status"] == "OK"
    # Departed-snapshot probes are skipped before the retention
    # migration lands (those columns do not exist on main yet).
    assert "departed_records_missing_name" in body["components"]["data_integrity"]["details"]["skipped_probes"]
    assert body["components"]["data_flow"]["status"] == "OK"
    assert body["components"]["security"]["status"] == "OK"
    assert body["components"]["security"]["details"]["active_sessions"] == 1
    assert body["components"]["user_activity"]["status"] == "OK"
    assert body["components"]["minio"]["status"] == "DISABLED"
    assert body["components"]["redis"]["status"] == "DISABLED"


# ----------------------------------------------------------------------
# Threshold rules, one component at a time
# ----------------------------------------------------------------------

def test_security_warning_on_failed_logins(world):
    _login(world.session, world.emp, success=True)
    for _ in range(12):
        _login(world.session, world.emp, success=False)
    body = _get(world)
    assert body["components"]["security"]["status"] == "WARNING"
    assert body["components"]["security"]["details"]["failed_logins_1h"] == 12
    assert body["overall"] == "WARNING"


def test_integrity_fail_on_bad_period(world):
    _login(world.session, world.emp, success=True)
    now = datetime.now(UTC).replace(tzinfo=None)
    world.session.add(KPIPeriod(
        label="Broken", start_date=now + timedelta(days=1),
        end_date=now, status=KPIPeriodStatus.OPEN, is_deleted=False,
    ))
    world.session.commit()
    body = _get(world)
    assert body["components"]["data_integrity"]["status"] == "FAIL"
    assert "invalid_period_dates" in body["components"]["data_integrity"]["details"]["failed_probes"]
    assert body["overall"] == "FAIL"


def test_flow_warning_when_logins_but_no_flow(world):
    _login(world.session, world.emp, success=True)
    body = _get(world)
    assert body["components"]["data_flow"]["status"] == "WARNING"
    assert body["components"]["data_flow"]["details"]["last_24h"]["total"] == 0


def test_activity_warning_after_quiet_week(world):
    _login(
        world.session, world.emp, success=True,
        when=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=8),
    )
    body = _get(world)
    assert body["components"]["user_activity"]["status"] == "WARNING"
    # Activity is never a FAIL, and silence alone is not a flow
    # problem (nobody signed in, so the flow warning stays off).
    assert body["components"]["data_flow"]["status"] == "OK"
    assert body["overall"] == "WARNING"


def test_redis_fail_when_unreachable(world, monkeypatch):
    monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:6399/0")
    _login(world.session, world.emp, success=True)
    body = _get(world)
    assert body["components"]["redis"]["status"] == "FAIL"
    assert body["overall"] == "FAIL"


# ----------------------------------------------------------------------
# Cache + status-change-only logging
# ----------------------------------------------------------------------

def test_cache_and_change_only_logging(world, monkeypatch):
    calls = []

    async def fake_log(level, event_type, message, details=None):
        calls.append((level, event_type, message, details))

    monkeypatch.setattr(health_module, "record_system_log", fake_log)
    _login(world.session, world.emp, success=True)
    now = datetime.now(UTC).replace(tzinfo=None)
    world.session.add(KPIPeriod(
        label="Broken", start_date=now + timedelta(days=1),
        end_date=now, status=KPIPeriodStatus.OPEN, is_deleted=False,
    ))
    world.session.commit()

    first = _get(world)
    assert first["overall"] == "FAIL"
    integrity_logs = [c for c in calls if c[3]["component"] == "data_integrity"]
    assert len(integrity_logs) == 1
    assert integrity_logs[0][1] == "HEALTH_CHECK"
    assert integrity_logs[0][0] == LogLevel.ERROR
    assert integrity_logs[0][3]["status"] == "FAIL"
    # The flow warning in this world (logins but no saved work
    # yet) legitimately logs once too — DISABLED components never do.
    assert len(calls) == 2
    assert {c[3]["component"] for c in calls} == {"data_integrity", "data_flow"}

    # Immediate second call is cached: same timestamp, no new log.
    second = _get(world)
    assert second["checked_at"] == first["checked_at"]
    assert len(calls) == 2

    # Fresh check, same broken state: still no new log.
    health_module.reset_health_cache()
    _get(world)
    assert len(calls) == 2

    # Heal the period: one recovery log for integrity only (the
    # flow warning is unchanged, so it stays silent).
    world.session.query(KPIPeriod).delete()
    world.session.commit()
    health_module.reset_health_cache()
    healed = _get(world)
    assert healed["components"]["data_integrity"]["status"] == "OK"
    assert len(calls) == 3
    assert calls[2][0] == LogLevel.INFO
    assert calls[2][3]["component"] == "data_integrity"
    assert calls[2][3]["status"] == "OK"


def test_health_check_mapper_keeps_plain_message():
    message = "System health: database connection failed."
    assert plain_message_for("HEALTH_CHECK", LogLevel.ERROR, message) == message
    # A technical-looking message never leaks through the mapper.
    assert "SELECT" not in plain_message_for(
        "HEALTH_CHECK", LogLevel.ERROR, "SELECT 1 failed: connection refused"
    )
