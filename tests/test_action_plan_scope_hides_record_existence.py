from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException, status

from app.modules.kpi import action_plan_router


class _Scalars:
    def __init__(self, value):
        self.value = value

    def first(self):
        return self.value


class _Result:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return _Scalars(self.value)


class _DB:
    def __init__(self, record):
        self.record = record

    async def execute(self, _statement):
        return _Result(self.record)


async def _deny_scope(_db, _current_user, _target_user_id):
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Access denied for this user's organizational scope.",
    )


async def _http_error(call):
    with pytest.raises(HTTPException) as raised:
        await call()
    return raised.value


@pytest.mark.asyncio
async def test_get_action_plan_hides_existing_out_of_scope_record(monkeypatch):
    record_id = uuid4()
    record = SimpleNamespace(id=record_id, user_id=uuid4(), is_deleted=False)
    monkeypatch.setattr(action_plan_router, "enforce_permission", lambda *_: None)
    monkeypatch.setattr(action_plan_router, "enforce_user_scope", _deny_scope)

    unauthorized = await _http_error(
        lambda: action_plan_router.get_action_plan(
            record_id, _DB(record), SimpleNamespace(id=uuid4())
        )
    )
    missing = await _http_error(
        lambda: action_plan_router.get_action_plan(
            uuid4(), _DB(None), SimpleNamespace(id=uuid4())
        )
    )

    assert unauthorized.status_code == status.HTTP_404_NOT_FOUND
    assert unauthorized.detail == "KPI Record not found."
    assert (unauthorized.status_code, unauthorized.detail) == (
        missing.status_code,
        missing.detail,
    )


@pytest.mark.asyncio
async def test_submit_action_plan_hides_existing_out_of_scope_record(monkeypatch):
    record_id = uuid4()
    record = SimpleNamespace(
        id=record_id,
        user_id=uuid4(),
        is_deleted=False,
    )
    monkeypatch.setattr(action_plan_router, "enforce_permission", lambda *_: None)
    monkeypatch.setattr(action_plan_router, "enforce_user_scope", _deny_scope)
    payload = SimpleNamespace(kpi_record_id=record_id)

    unauthorized = await _http_error(
        lambda: action_plan_router.submit_action_plan(
            payload, _DB(record), SimpleNamespace(id=uuid4())
        )
    )
    missing = await _http_error(
        lambda: action_plan_router.submit_action_plan(
            SimpleNamespace(kpi_record_id=uuid4()),
            _DB(None),
            SimpleNamespace(id=uuid4()),
        )
    )

    assert unauthorized.status_code == status.HTTP_404_NOT_FOUND
    assert unauthorized.detail == "KPI Record not found."
    assert (unauthorized.status_code, unauthorized.detail) == (
        missing.status_code,
        missing.detail,
    )
