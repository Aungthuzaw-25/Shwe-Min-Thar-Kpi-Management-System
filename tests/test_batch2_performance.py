"""Batch 2 performance work: session_timeout wiring + dashboard cache.

Covered here:
- ``create_access_token`` honors an explicit expiry override.
- ``get_session_timeout_minutes`` reads the ``security`` settings row with
  an env fallback and clamping, and never raises on storage errors.
- ``app.core.cache.cached_json`` fails open when Redis is not configured.
- System Settings -> Clear Cache returns 501 when no cache is reachable
  (truthful) and clears the dashboard namespace when one is.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import HTTPException

from app.core import cache as cache_module
from app.core import security
from app.core.config import settings
from app.core.password_policy import get_session_timeout_minutes
from app.modules.system import router as system_router


# ---------------------------------------------------------------------------
# create_access_token expiry override
# ---------------------------------------------------------------------------

def _claims(token: str) -> dict:
    return jwt.decode(token, options={"verify_signature": False})


def test_create_access_token_default_expiry_uses_env_minutes():
    claims = _claims(security.create_access_token({"sub": "a@example.com"}))
    assert claims["exp"] - claims["iat"] == settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60


def test_create_access_token_honors_custom_expiry():
    claims = _claims(
        security.create_access_token(
            {"sub": "a@example.com"}, expires_delta=timedelta(minutes=45)
        )
    )
    assert claims["exp"] - claims["iat"] == 45 * 60


# ---------------------------------------------------------------------------
# get_session_timeout_minutes
# ---------------------------------------------------------------------------

class _FakeScalars:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def scalars(self):
        return _FakeScalars(self._row)


class _FakeDb:
    def __init__(self, row=None, *, raises=False):
        self._row = row
        self._raises = raises

    async def execute(self, _stmt):
        if self._raises:
            raise RuntimeError("storage unavailable")
        return _FakeResult(self._row)


def _security_row(value):
    return SimpleNamespace(key="security", value=value)


@pytest.mark.asyncio
async def test_session_timeout_reads_panel_value():
    db = _FakeDb(_security_row({"session_timeout": 90}))
    assert await get_session_timeout_minutes(db) == 90


@pytest.mark.asyncio
async def test_session_timeout_falls_back_to_env_when_missing_or_invalid():
    env_default = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    assert await get_session_timeout_minutes(_FakeDb(None)) == env_default
    assert await get_session_timeout_minutes(_FakeDb(_security_row({}))) == env_default
    assert (
        await get_session_timeout_minutes(_FakeDb(_security_row({"session_timeout": "abc"})))
        == env_default
    )
    assert (
        await get_session_timeout_minutes(_FakeDb(_security_row({"session_timeout": 0})))
        == env_default
    )


@pytest.mark.asyncio
async def test_session_timeout_is_clamped():
    assert await get_session_timeout_minutes(_FakeDb(_security_row({"session_timeout": 1}))) == 5
    assert (
        await get_session_timeout_minutes(_FakeDb(_security_row({"session_timeout": 99999})))
        == 60 * 24
    )


@pytest.mark.asyncio
async def test_session_timeout_never_raises_on_storage_error():
    db = _FakeDb(raises=True)
    assert await get_session_timeout_minutes(db) == settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES


# ---------------------------------------------------------------------------
# app.core.cache fail-open behavior
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cached_json_fails_open_without_redis(monkeypatch):
    monkeypatch.setattr(cache_module.settings, "REDIS_URL", None)
    monkeypatch.setattr(cache_module, "_client", None)
    monkeypatch.setattr(cache_module, "_client_unavailable", False)

    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        return {"calls": calls}

    first = await cache_module.cached_json("dashboard", "k", 60, factory)
    second = await cache_module.cached_json("dashboard", "k", 60, factory)
    # No Redis -> nothing is cached; the factory runs every time.
    assert first == {"calls": 1}
    assert second == {"calls": 2}
    assert await cache_module.cache_available() is False
    assert await cache_module.invalidate_prefix("dashboard") == 0


# ---------------------------------------------------------------------------
# Clear Cache endpoint: truthful 501, real clear when a cache exists
# ---------------------------------------------------------------------------

def _super_admin():
    return SimpleNamespace(
        id="00000000-0000-0000-0000-000000000001",
        role=SimpleNamespace(name="Super Admin"),
    )


@pytest.mark.asyncio
async def test_clear_cache_stays_truthful_without_cache(monkeypatch):
    monkeypatch.setattr(system_router, "enforce_permission", lambda *_args: None)
    monkeypatch.setattr(system_router, "cache_available", AsyncMock(return_value=False))
    with pytest.raises(HTTPException) as exc:
        await system_router.clear_system_cache(None, _super_admin())
    assert exc.value.status_code == 501


@pytest.mark.asyncio
async def test_clear_cache_clears_dashboard_namespace(monkeypatch):
    monkeypatch.setattr(system_router, "enforce_permission", lambda *_args: None)
    monkeypatch.setattr(system_router, "cache_available", AsyncMock(return_value=True))
    invalidate = AsyncMock(return_value=7)
    monkeypatch.setattr(system_router, "invalidate_prefix", invalidate)
    result = await system_router.clear_system_cache(None, _super_admin())
    assert result["success"] is True
    assert result["cleared_keys"] == 7
    invalidate.assert_awaited_once_with("dashboard")
