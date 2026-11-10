"""RUM beacon endpoint (POST /api/v1/system/rum) — 2026-11-06.

Log-only telemetry, no DB table, no auth (sendBeacon cannot send
Bearer/CSRF headers — see app/modules/system/rum.py). The endpoint
takes no database dependency, so these tests use the app directly.
"""

import json
import time

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import DashboardCacheInvalidationMiddleware

client = TestClient(main_module.app, raise_server_exceptions=False)

RUM_URL = "/api/v1/system/rum"

VALID_BEACON = {
    "route": "/superadmin/dashboard",
    "ttfb_ms": 1234.5,
    "dom_content_loaded_ms": 2100,
    "load_ms": 3400,
    "first_paint_ms": 900,
    "first_contentful_paint_ms": 1100,
    "largest_contentful_paint_ms": 2600,
    "cls": 0.02,
    "connection": "4g",
    "navigation_type": "navigate",
}


def _post(payload, **kwargs):
    headers = kwargs.pop("headers", {})
    if isinstance(payload, dict):
        return client.post(RUM_URL, json=payload, headers=headers, **kwargs)
    return client.post(RUM_URL, content=payload, headers=headers, **kwargs)


def test_valid_beacon_accepted():
    response = _post(VALID_BEACON)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_minimal_beacon_route_only_accepted():
    response = _post({"route": "/dashboard"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_beacon_written_to_log_without_identity(caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="app.modules.system.rum"):
        response = _post(VALID_BEACON)
    assert response.status_code == 200
    line = next(
        record.getMessage()
        for record in caplog.records
        if record.name == "app.modules.system.rum"
    )
    assert line.startswith("rum route=/superadmin/dashboard")
    assert "ttfb_ms=1234.5" in line
    assert "largest_contentful_paint_ms=2600" in line
    assert "cls=0.02" in line
    # No identity in the log line: no IP, no user agent, no user id.
    assert "127.0.0.1" not in line
    assert "Mozilla" not in line
    assert "user_id" not in line


def test_unknown_fields_dropped_not_rejected():
    payload = dict(VALID_BEACON)
    payload["user_id"] = "someone"
    payload["user_agent"] = "Mozilla/5.0"
    payload["full_url"] = "https://example.com/superadmin/dashboard"
    response = _post(payload)
    assert response.status_code == 200


def test_missing_route_rejected():
    response = _post({"ttfb_ms": 100})
    assert response.status_code == 422


@pytest.mark.parametrize(
    "route",
    [
        "superadmin/dashboard",  # no leading slash
        "/superadmin/dashboard?view=general",  # query string
        "/superadmin/dashboard#section",  # fragment
        "/superadmin/users/550e8400-e29b-41d4-a716-446655440000/profile",  # UUID id
        "/" + "a" * 128,  # too long
    ],
)
def test_bad_route_rejected(route):
    response = _post({"route": route})
    assert response.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"route": "/dashboard", "ttfb_ms": -1},  # negative
        {"route": "/dashboard", "ttfb_ms": 600_001},  # over cap
        {"route": "/dashboard", "cls": 1.5},  # CLS is 0-1
        {"route": "/dashboard", "cls": -0.1},
        {"route": "/dashboard", "connection": "5g"},  # not a known bucket
        {"route": "/dashboard", "navigation_type": "teleport"},
        {"route": "/dashboard", "ttfb_ms": "fast"},  # not a number
    ],
)
def test_bad_values_rejected(payload):
    response = _post(payload)
    assert response.status_code == 422


def test_oversized_body_rejected_413():
    # A huge unknown field: the size check must fire before validation
    # would otherwise drop the field and answer 200.
    big = {"route": "/dashboard", "junk": "x" * 5000}
    response = _post(json.dumps(big))
    assert response.status_code == 413


def test_beacon_with_cookie_but_no_csrf_headers_accepted():
    # sendBeacon sends the session cookie and cannot send CSRF headers;
    # the endpoint is CSRF-exempt, so this must NOT 403.
    response = _post(
        VALID_BEACON,
        headers={"Cookie": "access_token=pretend-session-token"},
    )
    assert response.status_code == 200


def test_beacon_does_not_purge_dashboard_cache(monkeypatch):
    """A RUM POST must not trigger the dashboard-cache purge.

    Without the exclusion in DashboardCacheInvalidationMiddleware,
    every page load's beacon would bust every user's cached summary.
    A scratch app proves the exclusion (not just the tuple): the same
    middleware purges for another POST path but not for /rum.
    """
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def _ok(request):
        return JSONResponse({"status": "ok"})

    scratch = Starlette(
        routes=[
            Route("/api/v1/system/rum", _ok, methods=["POST"]),
            Route("/api/v1/tasks/x", _ok, methods=["POST"]),
        ],
        middleware=[Middleware(DashboardCacheInvalidationMiddleware)],
    )
    calls = []

    async def _fake_purge():
        calls.append(1)
        return 1

    monkeypatch.setattr(
        "app.core.cache.invalidate_dashboard_summaries", _fake_purge
    )
    with TestClient(scratch, raise_server_exceptions=False) as scratch_client:
        assert scratch_client.post("/api/v1/system/rum").status_code == 200
        time.sleep(0.2)  # let the middleware's background task run, if any
        assert calls == []
        assert scratch_client.post("/api/v1/tasks/x").status_code == 200
        time.sleep(0.2)
        assert calls == [1]  # control: the purge fires for other POSTs


def test_rate_limit_per_ip():
    # A dedicated X-Forwarded-For bucket so this test cannot consume
    # (or be affected by) the shared test-client bucket.
    headers = {"X-Forwarded-For": "203.0.113.77"}
    for _ in range(60):
        response = _post(VALID_BEACON, headers=headers)
        assert response.status_code == 200
    response = _post(VALID_BEACON, headers=headers)
    assert response.status_code == 429


def test_beacon_does_not_break_normal_api_calls():
    # The CSRF exemption + cache exclusion are path-scoped; an ordinary
    # public API call still works after beacons.
    response = client.get("/api/v1/system/branding/public")
    assert response.status_code == 200
    response = _post(VALID_BEACON)
    assert response.status_code == 200
    response = client.get("/api/v1/system/branding/public")
    assert response.status_code == 200
