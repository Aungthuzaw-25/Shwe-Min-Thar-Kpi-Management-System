from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.core import dependencies


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/protected",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
        }
    )


@pytest.mark.asyncio
async def test_refresh_token_cannot_authenticate_as_access_token(monkeypatch):
    monkeypatch.setattr(
        dependencies.jwt,
        "decode",
        lambda *args, **kwargs: {"type": "refresh", "sub": "admin@example.com"},
    )

    with pytest.raises(HTTPException) as error:
        await dependencies.get_current_user(
            request=_request(),
            token="refresh-token",
            db=SimpleNamespace(),
        )

    assert error.value.status_code == 401
    assert error.value.detail == "Invalid token type."
