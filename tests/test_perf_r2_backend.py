"""Performance Round 2 backend fixes.

Covered here (more added per fix):
- Dashboard cache invalidation is fail-open: Redis trouble can never
  break the request that triggered the purge.
- The purge removes only the dashboard namespace keys.
- The invalidation middleware purges after successful API writes and
  leaves reads, failures, and auth/account paths alone.
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.core import cache as cache_module
from app.main import DashboardCacheInvalidationMiddleware


class FakeRedis:
    def __init__(self, keys=(), *, fail_on_scan=False, fail_on_delete=False):
        self.keys = list(keys)
        self.fail_on_scan = fail_on_scan
        self.fail_on_delete = fail_on_delete
        self.deleted = []
        self.scan_patterns = []

    async def scan_iter(self, match=None, count=None):
        self.scan_patterns.append(match)
        if self.fail_on_scan:
            raise ConnectionError("redis went away")
        for key in list(self.keys):
            yield key

    async def delete(self, key):
        if self.fail_on_delete:
            raise ConnectionError("redis went away")
        self.deleted.append(key)
        return 1


@pytest.fixture()
def fake_cache_client(monkeypatch):
    def install(client):
        monkeypatch.setattr(cache_module, "_client", client)
        monkeypatch.setattr(cache_module, "_client_unavailable", False)
        return client

    return install


async def test_invalidate_prefix_deletes_dashboard_keys(fake_cache_client):
    client = fake_cache_client(
        FakeRedis(["kpi:cache:dashboard:superadmin-summary"])
    )
    deleted = await cache_module.invalidate_prefix("dashboard")
    assert deleted == 1
    assert client.deleted == ["kpi:cache:dashboard:superadmin-summary"]
    assert client.scan_patterns == ["kpi:cache:dashboard:*"]


async def test_invalidate_prefix_fail_open_on_scan_error(fake_cache_client):
    fake_cache_client(FakeRedis(fail_on_scan=True))
    # Must not raise: the write that triggered this purge already
    # succeeded and its response must not turn into a 500.
    assert await cache_module.invalidate_prefix("dashboard") == 0


async def test_invalidate_prefix_fail_open_on_delete_error(fake_cache_client):
    fake_cache_client(
        FakeRedis(["kpi:cache:dashboard:admin-summary:1"], fail_on_delete=True)
    )
    assert await cache_module.invalidate_prefix("dashboard") == 0


async def test_invalidate_dashboard_summaries_targets_dashboard_namespace(
    fake_cache_client,
):
    client = fake_cache_client(FakeRedis(["kpi:cache:dashboard:x"]))
    assert await cache_module.invalidate_dashboard_summaries() == 1
    assert client.scan_patterns == ["kpi:cache:dashboard:*"]


async def test_invalidate_prefix_without_client_is_zero(monkeypatch):
    monkeypatch.setattr(cache_module, "_client", None)
    monkeypatch.setattr(cache_module, "_client_unavailable", True)
    assert await cache_module.invalidate_prefix("dashboard") == 0


# ---------------------------------------------------------------------------
# Invalidation middleware: which requests purge the dashboard cache
# ---------------------------------------------------------------------------

class _PurgeRecorder:
    def __init__(self):
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        return 1


async def _dispatch(monkeypatch, method, path, status=200):
    recorder = _PurgeRecorder()
    monkeypatch.setattr(
        cache_module, "invalidate_dashboard_summaries", recorder
    )
    middleware = DashboardCacheInvalidationMiddleware(app=None)
    request = SimpleNamespace(
        method=method, url=SimpleNamespace(path=path)
    )

    async def call_next(_request):
        return SimpleNamespace(status_code=status)

    response = await middleware.dispatch(request, call_next)
    await asyncio.sleep(0)  # let the background purge task run
    await asyncio.sleep(0)
    return response, recorder


async def test_middleware_purges_after_successful_task_write(monkeypatch):
    response, recorder = await _dispatch(monkeypatch, "POST", "/api/v1/tasks")
    assert response.status_code == 200
    assert recorder.calls == 1


async def test_middleware_purges_after_user_and_kpi_writes(monkeypatch):
    for path in ("/api/v1/users/", "/api/v1/kpi/calculate", "/api/v1/targets/"):
        _response, recorder = await _dispatch(monkeypatch, "PUT", path)
        assert recorder.calls == 1, path


async def test_middleware_skips_reads_failures_and_auth_paths(monkeypatch):
    cases = [
        ("GET", "/api/v1/tasks", 200),
        ("POST", "/api/v1/tasks", 422),
        ("POST", "/api/v1/tasks", 500),
        ("POST", "/api/v1/auth/login", 200),
        ("POST", "/api/v1/auth/logout", 200),
        ("PUT", "/api/v1/account/me/profile", 200),
        ("POST", "/auth/login", 200),
    ]
    for method, path, status in cases:
        _response, recorder = await _dispatch(
            monkeypatch, method, path, status
        )
        assert recorder.calls == 0, (method, path, status)


# ---------------------------------------------------------------------------
# DB per-statement timeout (asyncpg command_timeout)
# ---------------------------------------------------------------------------

def test_postgres_connect_args_have_statement_timeout():
    from app.core.config import settings as app_settings
    from app.database.connection import _build_connect_args

    args = _build_connect_args(
        "postgresql+asyncpg://user:pass@db.example.com:5432/app"
    )
    assert args["statement_cache_size"] == 0
    assert args["command_timeout"] == app_settings.DB_COMMAND_TIMEOUT_SECONDS
    assert args["command_timeout"] == 60.0  # documented default
    assert args["ssl"] is True  # non-localhost + SSL required


def test_localhost_and_sqlite_connect_args():
    from app.database.connection import _build_connect_args

    local = _build_connect_args(
        "postgresql+asyncpg://user:pass@localhost:5432/app"
    )
    assert local["command_timeout"] == 60.0
    assert "ssl" not in local
    assert _build_connect_args("sqlite+aiosqlite:///./test.db") == {}


# ---------------------------------------------------------------------------
# Presigned URL: storage down must be a clear 503, never an empty link
# ---------------------------------------------------------------------------

def test_presigned_url_without_storage_raises_503():
    from fastapi import HTTPException

    from app.services.storage_service import StorageService

    service = StorageService.__new__(StorageService)
    service.s3_client = None
    service.bucket_name = "kpi-evidence"
    with pytest.raises(HTTPException) as exc_info:
        service.get_presigned_url("evidence/file.pdf")
    assert exc_info.value.status_code == 503
    assert "unavailable" in exc_info.value.detail.lower()


def test_presigned_url_with_storage_returns_url():
    from app.services.storage_service import StorageService

    class FakeS3:
        def generate_presigned_url(self, _op, Params, ExpiresIn):
            return f"https://storage.example/{Params['Key']}?exp={ExpiresIn}"

    service = StorageService.__new__(StorageService)
    service.s3_client = FakeS3()
    service.bucket_name = "kpi-evidence"
    assert (
        service.get_presigned_url("evidence/file.pdf", expiration=60)
        == "https://storage.example/evidence/file.pdf?exp=60"
    )


# ---------------------------------------------------------------------------
# Rate limiter: a Redis outage must not 500 rate-limited endpoints
# (live-verified in Round 2: default slowapi + dead Redis storage = 500;
# the limiter built by app.main now falls back to in-memory counting)
# ---------------------------------------------------------------------------

def test_rate_limiter_survives_dead_redis():
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded

    from app.main import _build_rate_limiter

    limiter = _build_rate_limiter("redis://127.0.0.1:6399/0")
    probe = FastAPI()
    probe.state.limiter = limiter
    probe.add_exception_handler(
        RateLimitExceeded, _rate_limit_exceeded_handler
    )

    @probe.get("/ping")
    @limiter.limit("3/minute")
    async def ping(request: Request):
        return {"ok": True}

    with TestClient(probe, raise_server_exceptions=False) as client:
        assert client.get("/ping").status_code == 200  # not 500
        client.get("/ping")
        client.get("/ping")
        # Limiting still applies (counted in memory while Redis is down)
        assert client.get("/ping").status_code == 429
