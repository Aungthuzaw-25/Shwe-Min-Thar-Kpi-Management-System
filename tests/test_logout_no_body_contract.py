from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core import security
from app.modules.auth import router as auth_router
from app.modules.auth.models import AuthSession, TokenBlacklist


class _Scalars:
    def __init__(self, values):
        self.values = values

    def first(self):
        return self.values[0] if self.values else None

    def all(self):
        return list(self.values)


class _Result:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return _Scalars(self.values)


class _LogoutDB:
    def __init__(self, session):
        self.session = session
        self.blacklisted_hashes = set()
        self.commits = 0

    async def execute(self, _statement):
        if self.blacklisted_hashes:
            return _Result(
                [SimpleNamespace(token_hash=value) for value in self.blacklisted_hashes]
            )
        return _Result([])

    def add(self, value):
        if isinstance(value, TokenBlacklist):
            self.blacklisted_hashes.add(value.token_hash)

    async def commit(self):
        self.commits += 1

    async def get(self, model, session_id):
        if model is AuthSession and str(session_id) == str(self.session.id):
            return self.session
        return None


@pytest.mark.asyncio
async def test_logout_without_request_body_revokes_session_and_clears_cookies():
    user_id, session_id = uuid4(), uuid4()
    user_email = "logout-contract@example.test"
    session = SimpleNamespace(
        id=session_id,
        user_id=user_id,
        remember_me=False,
        revoked_at=None,
    )
    db = _LogoutDB(session)
    token_data = {
        "sub": user_email,
        "user_id": str(user_id),
        "sid": str(session_id),
    }
    access_token = security.create_access_token(token_data)
    refresh_token = security.create_refresh_token(token_data)

    app = FastAPI()
    app.state.limiter = Limiter(key_func=get_remote_address)
    app.include_router(auth_router.router, prefix="/api/v1/auth")
    app.dependency_overrides[auth_router.get_db] = lambda: db
    try:
        with TestClient(app) as client:
            empty_object_response = client.post(
                "/api/v1/auth/logout",
                json={},
                headers={"Authorization": f"Bearer {access_token}"},
                cookies={"refresh_token": refresh_token},
            )
            assert empty_object_response.status_code == 422
            assert session.revoked_at is None
            assert db.commits == 0

            logout_response = client.post(
                "/api/v1/auth/logout",
                headers={"Authorization": f"Bearer {access_token}"},
                cookies={"refresh_token": refresh_token},
            )

            assert logout_response.status_code == 200
            assert logout_response.json()["message"] == (
                "Logged out and tokens revoked successfully."
            )
            assert session.revoked_at is not None
            assert db.commits == 1

            cookie_headers = logout_response.headers.get_list("set-cookie")
            for cookie_name in ("access_token", "refresh_token"):
                matching_cookie = next(
                    header
                    for header in cookie_headers
                    if header.startswith(f"{cookie_name}=")
                )
                assert "max-age=0" in matching_cookie.lower()
                assert "httponly" in matching_cookie.lower()

            refresh_response = client.post(
                "/api/v1/auth/refresh",
                cookies={"refresh_token": refresh_token},
            )
            assert refresh_response.status_code == 401
            assert db.blacklisted_hashes == {
                security.token_digest(access_token),
                security.token_digest(refresh_token),
            }
            assert not any(current.revoked_at is None for current in [session])
    finally:
        app.dependency_overrides.pop(auth_router.get_db, None)
