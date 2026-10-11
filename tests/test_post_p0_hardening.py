from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.background_jobs import reminders
from app.modules.kpi import engine as kpi_engine
from app.modules.kpi import router as kpi_router
from app.modules.kpi import service as kpi_service
from app.modules.system import router as system_router
from app.modules.tasks import router as tasks_router


class _Scalars:
    def __init__(self, values):
        self.values = list(values)

    def all(self):
        return list(self.values)

    def first(self):
        return self.values[0] if self.values else None


class _Result:
    def __init__(self, values):
        self.values = values

    def unique(self):
        return self

    def scalars(self):
        return _Scalars(self.values)


def _user(role_name, user_id=None):
    return SimpleNamespace(id=user_id or uuid4(), role=SimpleNamespace(name=role_name))


@pytest.mark.asyncio
async def test_formula_creation_persists_authoritative_position_and_rejects_cross_department(monkeypatch):
    department_id, other_department_id, position_id = uuid4(), uuid4(), uuid4()
    position = SimpleNamespace(
        id=position_id,
        name="Canonical Position",
        department_id=department_id,
        is_active=True,
        is_deleted=False,
    )
    created = []

    class Session:
        async def get(self, _model, requested_id):
            return position if requested_id == position_id else None

        def add(self, value):
            created.append(value)

        async def flush(self):
            return None

        async def commit(self):
            return None

        async def refresh(self, _value):
            return None

    formula_input = SimpleNamespace(
        name="Formula",
        description=None,
        region_town=None,
        department_id=department_id,
        position_id=position_id,
        position_name="Untrusted name",
        user_level="L2",
        categories=[],
        scope="DEPARTMENT",
        is_active=True,
        effective_month=None,
    )

    monkeypatch.setattr(
        kpi_service,
        "KPIFormula",
        lambda **kwargs: SimpleNamespace(id=uuid4(), **kwargs),
    )

    formula = await kpi_service.create_kpi_formula(Session(), formula_input)
    assert formula.position_id == position_id
    assert formula.position_name == "Canonical Position"

    with pytest.raises(HTTPException) as denied:
        await kpi_service._resolve_formula_position(
            Session(), other_department_id, position_id
        )
    assert denied.value.status_code == 400


@pytest.mark.asyncio
async def test_formula_engine_prefers_position_id_and_keeps_name_fallback():
    department_id, position_id = uuid4(), uuid4()
    user = SimpleNamespace(
        position_id=position_id,
        position=SimpleNamespace(name="Position", is_active=True, is_deleted=False),
        user_departments=[
            SimpleNamespace(
                department_id=department_id,
                user_level="L2",
                is_deleted=False,
            )
        ],
        region_town=None,
    )

    class Session:
        def __init__(self):
            self.statement = None

        async def execute(self, statement):
            self.statement = statement
            return _Result([])

    session = Session()
    assert await kpi_engine._find_fallback_formula(session, user, "2026-09") is None
    statement = str(session.statement)
    assert "position_id" in statement
    assert " OR " in statement


def test_global_kpi_setting_is_super_admin_only():
    with pytest.raises(HTTPException) as denied:
        kpi_router.allow_super_admins(_user("Admin"))
    assert denied.value.status_code == 403
    assert kpi_router.allow_super_admins(_user("Super Admin")).role.name == "Super Admin"


@pytest.mark.asyncio
async def test_clear_cache_is_truthful_when_no_application_cache_exists(monkeypatch):
    monkeypatch.setattr(system_router, "enforce_permission", lambda *_args: None)
    with pytest.raises(HTTPException) as unavailable:
        await system_router.clear_system_cache(None, _user("Super Admin"))
    assert unavailable.value.status_code == 501


@pytest.mark.asyncio
async def test_reminders_are_dispatched_once_per_task_per_24_hours(monkeypatch):
    now = datetime.now(reminders.timezone)
    due_task = SimpleNamespace(
        id=uuid4(),
        owner_id=uuid4(),
        title="Due task",
        due_date=now + timedelta(hours=2),
        last_reminder_sent=None,
    )
    already_reminded = SimpleNamespace(
        id=uuid4(),
        owner_id=uuid4(),
        title="Already reminded",
        due_date=now + timedelta(hours=2),
        last_reminder_sent=now,
    )
    notifications = []

    class Session:
        def __init__(self):
            self.commits = 0
            self.added = []

        async def execute(self, _statement):
            return _Result([due_task, already_reminded])

        def add(self, value):
            self.added.append(value)

        async def commit(self):
            self.commits += 1

    async def create_notification(_db, payload, *, commit):
        notifications.append((payload, commit))

    monkeypatch.setattr(reminders, "create_notification", create_notification)
    session = Session()
    await reminders.check_and_send_task_reminders(session)

    assert len(notifications) == 1
    assert notifications[0][1] is False
    assert due_task.last_reminder_sent is not None
    assert already_reminded.last_reminder_sent == now
    assert session.commits == 1


@pytest.mark.asyncio
async def test_task_authorization_ignores_soft_deleted_department_memberships():
    department_id, admin_id = uuid4(), uuid4()
    task = SimpleNamespace(
        id=uuid4(),
        owner_id=uuid4(),
        assigner_id=uuid4(),
        owner=SimpleNamespace(
            user_departments=[SimpleNamespace(department_id=department_id, is_deleted=True)]
        ),
        reviews=[],
    )

    class Session:
        def __init__(self):
            self.calls = 0

        async def execute(self, _statement):
            self.calls += 1
            if self.calls == 1:
                return _Result([task])
            return _Result([department_id])

    with pytest.raises(HTTPException) as denied:
        await tasks_router._authorized_task(Session(), task.id, _user("Admin", admin_id))
    assert denied.value.status_code == 404


def test_first_login_redirect_does_not_depend_on_stored_user_role_or_unsafe_csp():
    root = Path(__file__).resolve().parents[1]
    login_template = (root / "templates" / "auth" / "login.njk").read_text(encoding="utf-8")
    password_template = (root / "templates" / "auth" / "change_password.njk").read_text(encoding="utf-8")
    main_source = (root / "app" / "main.py").read_text(encoding="utf-8")

    assert "localStorage.setItem(\"user\", JSON.stringify(data.user));\n                        }\n                        window.location.href = '/auth/change-password'" not in login_template
    assert 'localStorage.getItem("user")' not in password_template
    assert "allowedPostLoginPaths" in login_template
    assert "unsafe-inline" not in main_source
    assert "unsafe-eval" not in main_source