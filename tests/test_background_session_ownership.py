import inspect
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks

from app.modules.kpi import router as kpi_router
from app.modules.kpi import service as kpi_service
from app.modules.kpi.schemas import BulkKpiCalculationRequest, CustomKpiCalculationRequest
from app.modules.notifications import service as notification_service


class _Session:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.closed = True


@pytest.mark.asyncio
async def test_custom_kpi_background_worker_owns_session_and_rolls_back_failed_user(monkeypatch):
    session = _Session()
    user_ids = [uuid4(), uuid4()]
    calls = []

    async def calculate(*, db, user_id, start_date, end_date):
        calls.append((db, user_id, start_date, end_date))
        if user_id == user_ids[0]:
            raise RuntimeError("calculation failed")

    monkeypatch.setattr(kpi_router, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(kpi_router, "calculate_custom_period_kpi", calculate)

    await kpi_router.run_custom_kpi_background(
        user_ids, datetime(2026, 1, 1), datetime(2026, 2, 1)
    )

    assert [call[1] for call in calls] == user_ids
    assert all(call[0] is session for call in calls)
    assert session.rollbacks == 1
    assert session.commits == 1
    assert session.closed


@pytest.mark.asyncio
async def test_bulk_kpi_background_callback_owns_session_and_commits(monkeypatch):
    session = _Session()
    user_ids = [uuid4()]
    received = []

    class _SessionFactory:
        def __call__(self):
            return session

    async def calculate(db, received_user_ids, start_date, end_date):
        received.append((db, received_user_ids, start_date, end_date))
        return {"total": 1, "success": 1, "failed": 0, "errors": []}

    monkeypatch.setattr(kpi_service, "AsyncSessionLocal", _SessionFactory())
    monkeypatch.setattr(kpi_service, "bulk_calculate_kpis", calculate)

    await kpi_service.run_bulk_kpi_calculation_background(
        user_ids, datetime(2026, 1, 1), datetime(2026, 2, 1)
    )

    assert received == [(session, user_ids, datetime(2026, 1, 1), datetime(2026, 2, 1))]
    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.closed


@pytest.mark.asyncio
async def test_bulk_kpi_background_callback_rolls_back_and_logs_failure(monkeypatch, caplog):
    session = _Session()

    class _SessionFactory:
        def __call__(self):
            return session

    async def fail_calculation(*_args):
        raise RuntimeError("calculation failed")

    monkeypatch.setattr(kpi_service, "AsyncSessionLocal", _SessionFactory())
    monkeypatch.setattr(kpi_service, "bulk_calculate_kpis", fail_calculation)

    await kpi_service.run_bulk_kpi_calculation_background(
        [uuid4()], datetime(2026, 1, 1), datetime(2026, 2, 1)
    )

    assert session.rollbacks == 1
    assert session.commits == 0
    assert session.closed
    assert "Background bulk KPI calculation failed" in caplog.text


@pytest.mark.asyncio
async def test_kpi_endpoints_register_background_callbacks_without_request_session(monkeypatch):
    user_ids = [uuid4()]
    registered = []

    class _BackgroundTasks:
        def add_task(self, callback, *args):
            registered.append((callback, args))

    async def authorized_users(_db, _current_user, requested_ids=None):
        return requested_ids or user_ids

    monkeypatch.setattr(kpi_router, "enforce_permission", lambda *_args: None)
    monkeypatch.setattr(kpi_router, "_authorized_active_user_ids", authorized_users)
    dates = {"start_date": datetime(2026, 1, 1), "end_date": datetime(2026, 2, 1)}
    db = object()

    await kpi_router.trigger_custom_calculation(
        CustomKpiCalculationRequest(**dates),
        _BackgroundTasks(),
        db,
        SimpleNamespace(),
    )
    await kpi_router.bulk_calculate_endpoint(
        BulkKpiCalculationRequest(**dates, user_ids=user_ids, run_in_background=True),
        _BackgroundTasks(),
        db,
        SimpleNamespace(),
    )

    assert registered[0][0] is kpi_router.run_custom_kpi_background
    assert len(registered[0][1]) == 3
    assert registered[1][0] is kpi_service.run_bulk_kpi_calculation_background
    assert len(registered[1][1]) == 3
    assert all(db not in args for _, args in registered)


@pytest.mark.asyncio
async def test_notification_service_owns_session_without_session_callback_argument(monkeypatch):
    session = _Session()
    captured = {}

    class _SessionFactory:
        def __call__(self):
            return session

    async def create_notification(db, data, *, commit):
        captured["session"] = db
        captured["data"] = data
        captured["commit"] = commit
        return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(notification_service, "AsyncSessionLocal", _SessionFactory())
    monkeypatch.setattr(notification_service, "create_notification", create_notification)
    monkeypatch.setattr(
        notification_service,
        "get_user_by_id",
        lambda _db, _user_id: _async_user("user@example.test"),
    )
    background_tasks = BackgroundTasks()
    user_id = uuid4()

    result = await notification_service.NotificationService.trigger_notification(
        user_id,
        "Title",
        "Message",
        "TEST",
        background_tasks,
        channel="BOTH",
    )

    assert "db" not in inspect.signature(
        notification_service.NotificationService.trigger_notification
    ).parameters
    assert captured["session"] is session
    assert captured["data"]["user_id"] == user_id
    assert captured["commit"] is False
    assert result.id
    assert session.commits == 1
    assert session.rollbacks == 0
    assert session.closed
    assert len(background_tasks.tasks) == 1


async def _async_user(email):
    return SimpleNamespace(email=email)
