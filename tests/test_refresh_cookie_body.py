"""Refresh body tokens: cookie clients get cookies only.

A future XSS could call /refresh with the victim's cookies
(the csrf cookie is JS-readable by design) and READ fresh
tokens from the JSON body. Cookie-authenticated refresh
therefore returns null body tokens — the HttpOnly cookies
already carry the rotated tokens. Bearer/API clients (body
token presented) still get body tokens: they presented a
token explicitly and cannot use cookies.
"""

import asyncio
from datetime import datetime

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import security
from app.database.connection import get_db
from app.modules.auth.models import AuthSession
from app.modules.auth.router import router as auth_router
from app.modules.users.models import Role, User

FUTURE = datetime(2027, 1, 1)


def _run(coro_factory, tmp_path):
    db_file = tmp_path / "refreshbody.db"
    sync_engine = create_engine(f"sqlite:///{db_file}")
    from app.database.base import BaseModel

    BaseModel.metadata.create_all(sync_engine)
    sync_engine.dispose()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}", poolclass=None
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _main():
        async with factory() as db:
            return await coro_factory(db)

    try:
        return asyncio.run(_main())
    finally:
        asyncio.run(engine.dispose())


def _app(db):
    app = FastAPI()
    app.include_router(auth_router, prefix="/api/v1/auth")

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    return app


async def _world(db):
    role = Role(name="Employee")
    db.add(role)
    await db.flush()
    user = User(
        email="refresh@example.test",
        hashed_password="x",
        first_name="Refresh",
        last_name="User",
        role_id=role.id,
        is_active=True,
        is_deleted=False,
    )
    db.add(user)
    await db.flush()
    session = AuthSession(
        user_id=user.id,
        refresh_token_hash="pending",
        device_id=None,
        user_agent=None,
        ip_address=None,
        remember_me=False,
        expires_at=FUTURE,
        last_used_at=datetime(2026, 10, 29),
        revoked_at=None,
    )
    db.add(session)
    await db.flush()
    refresh = security.create_refresh_token(
        {
            "sub": user.email,
            "user_id": str(user.id),
            "sid": str(session.id),
        },
        remember_me=False,
    )
    session.refresh_token_hash = security.token_digest(refresh)
    await db.commit()
    return user, session, refresh


def _set_cookies(headers):
    """Map cookie name -> value from Set-Cookie headers."""
    cookies = {}
    for header in headers.get_list("set-cookie"):
        pair = header.split(";", 1)[0]
        if "=" in pair:
            name, value = pair.split("=", 1)
            cookies[name.strip()] = value.strip()
    return cookies


def test_cookie_refresh_returns_null_body_tokens_but_rotates_cookies(tmp_path):
    async def _case(db):
        _user, _session, refresh = await _world(db)
        transport = ASGITransport(app=_app(db))
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/auth/refresh",
                cookies={"refresh_token": refresh},
            )
        assert resp.status_code == 200
        body = resp.json()
        # Same keys, null values: the least-breaking shape.
        assert body["access_token"] is None
        assert body["refresh_token"] is None
        assert body["token_type"] == "bearer"
        cookies = _set_cookies(resp.headers)
        assert cookies["access_token"]
        assert cookies["refresh_token"]
        assert cookies["refresh_token"] != refresh
        assert "httponly" in resp.headers["set-cookie"].lower()
        # Rotation: the presented refresh token is now single-use.
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            reuse = await client.post(
                "/api/v1/auth/refresh",
                cookies={"refresh_token": refresh},
            )
        assert reuse.status_code == 401

    _run(_case, tmp_path)


def test_bearer_refresh_still_returns_body_tokens(tmp_path):
    async def _case(db):
        _user, _session, refresh = await _world(db)
        transport = ASGITransport(app=_app(db))
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": refresh},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"]
        assert body["refresh_token"]
        assert body["refresh_token"] != refresh
        # Cookies rotate for bearer clients too (unchanged).
        cookies = _set_cookies(resp.headers)
        assert cookies["access_token"]
        assert cookies["refresh_token"]

    _run(_case, tmp_path)


def test_body_token_wins_when_both_present(tmp_path):
    async def _case(db):
        _user, _session, refresh = await _world(db)
        transport = ASGITransport(app=_app(db))
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": refresh},
                cookies={"refresh_token": "stale-cookie-value"},
            )
        assert resp.status_code == 200
        assert resp.json()["access_token"]
        assert resp.json()["refresh_token"]

    _run(_case, tmp_path)


def test_refresh_without_any_token_still_401(tmp_path):
    async def _case(db):
        await _world(db)
        transport = ASGITransport(app=_app(db))
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.post("/api/v1/auth/refresh")
        assert resp.status_code == 401

    _run(_case, tmp_path)
