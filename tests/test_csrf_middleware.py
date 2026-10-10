import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from app.main import CSRFMiddleware, app


def request(method="POST", headers=None, cookies=None, path="/api/v1/tasks"):
    header_items = [(b"host", b"testserver")]
    for key, value in (headers or {}).items():
        header_items.append((key.lower().encode(), value.encode()))
    if cookies:
        header_items.append(
            (b"cookie", "; ".join(f"{key}={value}" for key, value in cookies.items()).encode())
        )
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": header_items,
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 1234),
        }
    )


@pytest.mark.asyncio
async def test_cookie_mutation_requires_matching_csrf_token():
    middleware = CSRFMiddleware(lambda scope, receive, send: None)
    response = await middleware.dispatch(
        request(
            cookies={"access_token": "session", "csrf_token": "expected"},
            path="/api/v1/auth/change-password",
        ),
        lambda _: PlainTextResponse("ok"),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_cookie_mutation_rejects_missing_csrf_cookie():
    middleware = CSRFMiddleware(lambda scope, receive, send: None)
    response = await middleware.dispatch(
        request(
            headers={"X-CSRF-Token": "expected"},
            cookies={"access_token": "session"},
            path="/api/v1/auth/change-password",
        ),
        ok_response,
    )
    assert response.status_code == 403


async def ok_response(_):
    return PlainTextResponse("ok")


@pytest.mark.asyncio
async def test_matching_csrf_token_allows_cookie_mutation():
    middleware = CSRFMiddleware(lambda scope, receive, send: None)
    response = await middleware.dispatch(
        request(
            headers={"X-CSRF-Token": "expected"},
            cookies={"access_token": "session", "csrf_token": "expected"},
            path="/api/v1/auth/change-password",
        ),
        ok_response,
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_cookie_mutation_rejects_mismatched_csrf_token():
    middleware = CSRFMiddleware(lambda scope, receive, send: None)
    response = await middleware.dispatch(
        request(
            headers={"X-CSRF-Token": "different"},
            cookies={"access_token": "session", "csrf_token": "expected"},
            path="/api/v1/auth/change-password",
        ),
        ok_response,
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_bearer_api_mutation_is_not_blocked_by_csrf():
    middleware = CSRFMiddleware(lambda scope, receive, send: None)
    response = await middleware.dispatch(
        request(
            headers={"Authorization": "Bearer api-token"},
            cookies={"access_token": "browser-session"},
        ),
        ok_response,
    )
    assert response.status_code == 200


def test_cors_preflight_is_handled_before_application_middleware():
    with TestClient(app) as client:
        response = client.options(
            "/api/v1/health",
            headers={
                "Origin": "http://localhost:8000",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:8000"
