from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.main import app
import app.modules.auth.router as auth_router
import app.modules.kpi.dashboard_router as dashboard_router


class FakeResult:
    def __init__(self, value=None):
        self.value = value

    def scalars(self):
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


def test_login_route_returns_tokens(monkeypatch):
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
        hashed_password="hashed-password",
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
    assert payload["access_token"] == "fake-access-token"
    assert payload["refresh_token"] == "fake-refresh-token"
    assert payload["user"]["email"] == "alice@example.com"
    app.dependency_overrides.pop(auth_router.get_db, None)


def test_me_route_returns_current_user_context(monkeypatch):
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
