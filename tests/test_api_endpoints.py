from datetime import UTC
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import app.modules.activity_web as activity_web
import app.modules.auth.router as auth_router
import app.modules.kpi.dashboard_router as dashboard_router
import app.modules.notifications.router as notifications_router
import app.modules.system.router as system_router
from app.main import app


class FakeResult:
    def __init__(self, value=None):
        self.value = value

    def scalars(self):
        return self

    def unique(self):
        return self

    def first(self):
        return self.value

    def all(self):
        if isinstance(self.value, list):
            return self.value
        return [self.value] if self.value is not None else []

    def scalar(self):
        return self.value


class FakeDB:
    def __init__(self, user=None):
        self.user = user
        self.committed = 0
        self.added = []

    async def execute(self, stmt):
        return FakeResult(self.user)

    async def commit(self):
        self.committed += 1

    def add(self, obj):
        self.added.append(obj)


def test_account_profile_openapi_documents_not_found_response():
    operation = app.openapi()["paths"]["/api/v1/account/me"]["get"]

    assert operation["responses"]["404"]["description"] == "Account not found."


def test_login_route_returns_tokens(monkeypatch):
    monkeypatch.setenv("SEED_SUPER_ADMIN_PASSWORD", "Valid@Test123456")
    fake_user = SimpleNamespace(
        id="user-1",
        email="alice@example.com",
        first_name="Alice",
        last_name="Wonder",
        is_active=True,
        is_deleted=False,
        is_locked=False,
        lockout_until=None,
        failed_login_attempts=0,
        hashed_password="hashed-password",  # noqa: S106 - synthetic test fixture; not a production credential
        must_change_password=False,
        role=SimpleNamespace(name="L2"),
        position=SimpleNamespace(name="Officer"),
        user_departments=[SimpleNamespace(user_level="L2", department_id="dept-1", department=SimpleNamespace(name="Finance"))],
    )
    fake_db = FakeDB(fake_user)

    app.dependency_overrides[auth_router.get_db] = lambda: fake_db
    monkeypatch.setattr(auth_router.security, "verify_password", lambda plain, hashed: True)
    monkeypatch.setattr(auth_router.security, "create_access_token", lambda data: "fake-access-token")
    monkeypatch.setattr(auth_router.security, "create_refresh_token", lambda data: "fake-refresh-token")

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "alice@example.com", "password": "StrongPass!123"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["access_token"] == "fake-access-token"  # noqa: S105 - synthetic test fixture; not a production credential
    assert payload["refresh_token"] == "fake-refresh-token"  # noqa: S105 - synthetic test fixture; not a production credential
    assert payload["user"]["email"] == "alice@example.com"
    app.dependency_overrides.pop(auth_router.get_db, None)


def test_me_route_returns_current_user_context(monkeypatch):
    monkeypatch.setenv("SEED_SUPER_ADMIN_PASSWORD", "Valid@Test123456")
    fake_user = SimpleNamespace(
        id="user-1",
        first_name="Alice",
        last_name="Wonder",
        role=SimpleNamespace(name="L2"),
        position_id="pos-1",
        position=SimpleNamespace(name="Officer"),
        user_departments=[SimpleNamespace(user_level="L2", department_id="dept-1", department=SimpleNamespace(name="Finance"))],
    )
    fake_db = FakeDB(fake_user)

    app.dependency_overrides[auth_router.get_db] = lambda: fake_db
    app.dependency_overrides[auth_router.get_current_user] = lambda: fake_user

    with TestClient(app) as client:
        response = client.get("/api/v1/auth/me")

    assert response.status_code == 200
    payload = response.json()
    assert payload["user_id"] == "user-1"
    assert payload["role"] == "L2"
    assert payload["department_name"] == "Finance"
    app.dependency_overrides.pop(auth_router.get_db, None)
    app.dependency_overrides.pop(auth_router.get_current_user, None)


def test_dashboard_summary_route_uses_backend_summary(monkeypatch):
    monkeypatch.setenv("SEED_SUPER_ADMIN_PASSWORD", "Valid@Test123456")
    fake_user = SimpleNamespace(id="admin-1", role=SimpleNamespace(name="Super Admin"))

    app.dependency_overrides[dashboard_router.get_db] = lambda: FakeDB()
    app.dependency_overrides[dashboard_router.admin_checker] = lambda: fake_user
    async def fake_summary(db, user):
        return {
            "total_tasks": 12,
            "completed_tasks": 8,
            "pending_tasks": 4,
            "current_kpi_score": 82.5,
        }

    monkeypatch.setattr(dashboard_router, "_get_dashboard_task_summary", fake_summary)

    with TestClient(app) as client:
        response = client.get("/api/v1/superadmin/dashboard/summary/role-scope")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["total_tasks"] == 12
    assert payload["summary"]["completed_tasks"] == 8
    app.dependency_overrides.pop(dashboard_router.get_db, None)
    app.dependency_overrides.pop(dashboard_router.admin_checker, None)


def test_dashboards_have_distinct_summary_routes():
    super_paths = {getattr(route, "path", None) for route in dashboard_router.superadmin_router.routes}
    admin_paths = {getattr(route, "path", None) for route in dashboard_router.admin_router.routes}

    assert "/summary" in super_paths
    assert "/summary" in admin_paths
    assert "/summary" in {
        getattr(route, "path", None) for route in dashboard_router.unified_router.routes
    }


def test_unified_dashboard_route_is_registered():
    assert "/api/v1/dashboard/summary" in app.openapi()["paths"]


@pytest.mark.asyncio
async def test_notifications_choose_role_compatible_layout(monkeypatch):
    request = SimpleNamespace()
    db = SimpleNamespace()
    user = SimpleNamespace(
        id=uuid4(),
        role=SimpleNamespace(name="Super Admin"),
        first_name="Super",
        last_name="Admin",
        email="admin@example.com",
    )

    async def fake_context(db_arg, user_arg):
        return {}

    captured = {}

    class TemplateStub:
        def TemplateResponse(self, **kwargs):
            captured.update(kwargs["context"])
            return kwargs["context"]

    monkeypatch.setattr(activity_web, "get_employee_context", fake_context)
    monkeypatch.setattr(activity_web, "templates", TemplateStub())
    result = await activity_web.notifications_page(request, db, user)

    assert result["layout_template"] == "layouts/base.njk"
    assert result["active_page"] == "notifications"


@pytest.mark.asyncio
async def test_system_logs_page_contract_supports_filters_and_nullable_timestamps(monkeypatch):
    from app.models.system import LogLevel
    from app.modules.system.schemas import SystemEventLogPage

    user = SimpleNamespace(id=uuid4())
    log = SimpleNamespace(
        id=uuid4(),
        event_type="SYSTEM_TEST",
        level=LogLevel.INFO,
        message="System logging pipeline verification event",
        details={"safe": True},
        created_at=None,
    )

    async def fake_logs_page(db, **kwargs):
        assert kwargs["level"] == LogLevel.INFO
        assert kwargs["skip"] == 0
        return [log], 1

    monkeypatch.setattr(system_router, "enforce_permission", lambda *args: None)
    monkeypatch.setattr(system_router.repo, "get_recent_logs_page", fake_logs_page)

    payload = await system_router.read_system_logs(
        page=1,
        page_size=25,
        skip=None,
        limit=None,
        level=LogLevel.INFO,
        event_type=None,
        service=None,
        search=None,
        start=None,
        end=None,
        db=SimpleNamespace(),
        current_user=user,
    )

    validated = SystemEventLogPage.model_validate(payload)
    assert validated.total == 1
    assert validated.items[0].event_type == "SYSTEM_TEST"
    assert validated.items[0].created_at is None


@pytest.mark.asyncio
async def test_system_logs_database_failure_returns_safe_error(monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    async def fail(*args, **kwargs):
        raise SQLAlchemyError("database details must not reach the client")

    monkeypatch.setattr(system_router, "enforce_permission", lambda *args: None)
    monkeypatch.setattr(system_router.repo, "get_recent_logs_page", fail)
    db = SimpleNamespace()
    current_user = SimpleNamespace(id=uuid4())

    with pytest.raises(system_router.HTTPException) as exc_info:
        await system_router.read_system_logs(
            page=1, page_size=25, skip=None, limit=None, level=None,
            event_type=None, service=None, search=None, start=None, end=None,
            db=db, current_user=current_user,
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "System logs are temporarily unavailable."


@pytest.mark.asyncio
async def test_system_log_detail_rejects_invalid_id(monkeypatch):
    monkeypatch.setattr(system_router, "enforce_permission", lambda *args: None)
    db = SimpleNamespace()
    current_user = SimpleNamespace(id=uuid4())
    with pytest.raises(system_router.HTTPException) as exc_info:
        await system_router.read_system_log_detail(
            "not-a-uuid",
            db,
            current_user,
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_notification_detail_rejects_invalid_id():
    db = SimpleNamespace()
    current_user = SimpleNamespace(id=uuid4())
    with pytest.raises(notifications_router.HTTPException) as exc_info:
        await notifications_router.get_my_notification(
            "not-a-uuid", db, current_user
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_notification_detail_is_user_scoped_and_marks_read(monkeypatch):
    from datetime import datetime

    user_id = uuid4()
    notification = SimpleNamespace(
        id=uuid4(),
        user_id=user_id,
        title="Test",
        message="Details",
        notification_type="INFO",
        is_read=False,
        channel="DASHBOARD",
        group_key=None,
        created_at=datetime.now(UTC),
    )
    db = SimpleNamespace()

    async def fake_get(db_arg, notification_id, requested_user_id):
        assert requested_user_id == user_id
        return notification

    async def fake_commit():
        return None

    async def fake_refresh(obj):
        return None

    db.commit = fake_commit
    db.refresh = fake_refresh
    monkeypatch.setattr(notifications_router, "get_notification_by_user", fake_get)

    result = await notifications_router.get_my_notification(
        str(notification.id), db, SimpleNamespace(id=user_id)
    )
    assert result is notification
    assert notification.is_read is True
