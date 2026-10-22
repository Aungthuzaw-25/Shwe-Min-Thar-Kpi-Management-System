from fastapi import HTTPException, Request, status

from app.main import _http_exception_handler


def _request(path: str, accept: str, query: str = "") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 1234),
            "root_path": "",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query.encode(),
            "headers": [(b"accept", accept.encode())],
        }
    )


def test_unauthenticated_html_request_redirects_with_path_only():
    response = _http_exception_handler(
        _request("/tasks", "text/html", "token=must-not-be-forwarded"),
        HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Expired"),
    )

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == (
        "/auth/login?session_expired=true&next=%2Ftasks"
    )


def test_api_unauthorized_response_remains_json():
    response = _http_exception_handler(
        _request("/api/v1/tasks", "text/html,application/json"),
        HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Expired"),
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.body == b'{"detail":"Expired"}'


def test_html_forbidden_response_is_not_redirected():
    response = _http_exception_handler(
        _request("/admin/dashboard", "text/html"),
        HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Denied"),
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.headers["content-type"].startswith("text/html")
    assert b"Access denied" in response.body
    assert b"Denied" not in response.body


def test_api_forbidden_response_remains_json():
    response = _http_exception_handler(
        _request("/api/v1/tasks", "application/json"),
        HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Denied"),
    )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.body == b'{"detail":"Denied"}'


def test_login_route_does_not_redirect_back_to_login():
    response = _http_exception_handler(
        _request("/auth/login", "text/html"),
        HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Expired"),
    )

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/auth/login?session_expired=true"
