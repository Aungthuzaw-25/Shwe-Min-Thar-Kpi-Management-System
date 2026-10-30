"""Own per-device session list + single-session revoke.

Design: ~/workspace/your_files/agent-docs/database/
per-device-session-revoke-design.md (Database, Stage 2).

Fake values only: TEST-NET IPs (203.0.113.x / 198.51.100.x /
192.0.2.x), synthetic UAs, no real tokens or session ids leave
this file.
"""

import asyncio
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import security
from app.database.connection import get_db
from app.modules.auth.models import AuthSession
from app.modules.auth.router import router as auth_router
from app.modules.users.models import Role, User
from app.models.audit_log import AuditLog

CHROME_WIN = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
FIREFOX_MAC = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "Gecko/20100101 Firefox/133.0"
)
FUTURE = datetime(2027, 1, 1)
PAST = datetime(2026, 1, 1)


def _run(coro_factory, tmp_path):
    db_file = tmp_path / "mysessions.db"
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


def _access(user, session):
    return security.create_access_token(
        {
            "sub": user.email,
            "user_id": str(user.id),
            "sid": str(session.id),
        }
    )


def _refresh(user, session, remember_me=False):
    return security.create_refresh_token(
        {
            "sub": user.email,
            "user_id": str(user.id),
            "sid": str(session.id),
        },
        remember_me=remember_me,
    )


async def _world(db):
    role = Role(name="Employee")
    db.add(role)
    await db.flush()

    async def _user(email):
        user = User(
            email=email,
            hashed_password="x",
            first_name="Test",
            last_name="User",
            role_id=role.id,
            is_active=True,
            is_deleted=False,
        )
        db.add(user)
        await db.flush()
        return user

    async def _session(
        user, ua, ip, last_used, expires, revoked=None, remember_me=False
    ):
        session = AuthSession(
            user_id=user.id,
            refresh_token_hash="pending",
            device_id="hash-not-exposed",
            user_agent=ua,
            ip_address=ip,
            remember_me=remember_me,
            expires_at=expires,
            last_used_at=last_used,
            revoked_at=revoked,
        )
        db.add(session)
        await db.flush()
        # The stored hash must match a token whose sid is THIS row.
        real_refresh = _refresh(user, session, remember_me=remember_me)
        session.refresh_token_hash = security.token_digest(real_refresh)
        await db.flush()
        return session, real_refresh

    user_a = await _user("a@example.test")
    user_b = await _user("b@example.test")
    # Current row has the OLDER last_used on purpose: current-first
    # must beat recency in the ordering.
    cur_a, _ = await _session(
        user_a, CHROME_WIN, "203.0.113.7", datetime(2026, 10, 20), FUTURE
    )
    other_a, other_a_refresh = await _session(
        user_a,
        FIREFOX_MAC,
        "198.51.100.23",
        datetime(2026, 10, 28),
        FUTURE,
        remember_me=True,
    )
    null_ua_a, _ = await _session(
        user_a, None, "203.0.113.9", datetime(2026, 10, 25), FUTURE
    )
    revoked_a, _ = await _session(
        user_a,
        CHROME_WIN,
        "203.0.113.11",
        datetime(2026, 10, 26),
        FUTURE,
        revoked=datetime(2026, 10, 27),
    )
    expired_a, _ = await _session(
        user_a, CHROME_WIN, "203.0.113.13", datetime(2026, 10, 26), PAST
    )
    active_b, _ = await _session(
        user_b, CHROME_WIN, "192.0.2.55", datetime(2026, 10, 28), FUTURE
    )
    await db.commit()
    tokens = {
        "access_a": _access(user_a, cur_a),
        "access_other_a": _access(user_a, other_a),
        "refresh_other_a": other_a_refresh,
    }
    rows = {
        "cur_a": cur_a,
        "other_a": other_a,
        "null_ua_a": null_ua_a,
        "revoked_a": revoked_a,
        "expired_a": expired_a,
        "active_b": active_b,
    }
    return user_a, user_b, rows, tokens


def _app(db):
    app = FastAPI()
    app.include_router(auth_router, prefix="/api/v1/auth")

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    return app


async def _client(db):
    transport = ASGITransport(app=_app(db))
    return AsyncClient(transport=transport, base_url="http://test")


def test_list_shows_own_active_only_current_first(tmp_path):
    async def _case(db):
        user_a, _b, rows, tokens = await _world(db)
        async with await _client(db) as client:
            resp = await client.get(
                "/api/v1/auth/me/sessions",
                headers={"Authorization": f"Bearer {tokens['access_a']}"},
            )
        assert resp.status_code == 200
        body = resp.json()
        sessions = body["sessions"]
        assert [s["id"] for s in sessions] == [
            str(rows["cur_a"].id),
            str(rows["other_a"].id),
            str(rows["null_ua_a"].id),
        ]
        cur, other, null_ua = sessions
        assert cur["is_current"] is True
        assert cur["device_label"] == "Chrome on Windows"
        assert cur["ip_address"] == "203.0.113.7"
        assert cur["remember_me"] is False
        assert other["is_current"] is False
        assert other["device_label"] == "Firefox on macOS"
        assert other["remember_me"] is True
        assert null_ua["device_label"] == "Unknown device"
        for s in sessions:
            assert set(s) == {
                "id",
                "device_label",
                "last_used_at",
                "expires_at",
                "remember_me",
                "is_current",
                "ip_address",
            }
        text = resp.text
        assert "hash-not-exposed" not in text
        assert CHROME_WIN not in text
        assert FIREFOX_MAC not in text
        assert "refresh_token_hash" not in text
        assert "192.0.2.55" not in text

    _run(_case, tmp_path)


def test_list_requires_authentication(tmp_path):
    async def _case(db):
        await _world(db)
        async with await _client(db) as client:
            resp = await client.get("/api/v1/auth/me/sessions")
        assert resp.status_code == 401

    _run(_case, tmp_path)


def test_revoke_other_user_and_nonexistent_are_same_404(tmp_path):
    async def _case(db):
        _a, _b, rows, tokens = await _world(db)
        async with await _client(db) as client:
            headers = {"Authorization": f"Bearer {tokens['access_a']}"}
            resp_other = await client.delete(
                f"/api/v1/auth/me/sessions/{rows['active_b'].id}",
                headers=headers,
            )
            resp_missing = await client.delete(
                f"/api/v1/auth/me/sessions/{uuid4()}",
                headers=headers,
            )
        assert resp_other.status_code == 404
        assert resp_missing.status_code == 404
        assert resp_other.json() == resp_missing.json()
        row = await db.get(AuthSession, rows["active_b"].id)
        assert row.revoked_at is None

    _run(_case, tmp_path)


def test_revoke_own_other_session_then_refresh_fails(tmp_path):
    async def _case(db):
        _a, _b, rows, tokens = await _world(db)
        async with await _client(db) as client:
            headers = {"Authorization": f"Bearer {tokens['access_a']}"}
            resp = await client.delete(
                f"/api/v1/auth/me/sessions/{rows['other_a'].id}",
                headers=headers,
            )
            assert resp.status_code == 200
            row = await db.get(AuthSession, rows["other_a"].id)
            assert row.revoked_at is not None
            listed = await client.get(
                "/api/v1/auth/me/sessions", headers=headers
            )
            assert str(rows["other_a"].id) not in [
                s["id"] for s in listed.json()["sessions"]
            ]
            refresh = await client.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": tokens["refresh_other_a"]},
            )
        assert refresh.status_code == 401
        assert refresh.json()["detail"] == "Refresh session has been revoked."

    _run(_case, tmp_path)


def test_revoke_current_session_is_400_and_untouched(tmp_path):
    async def _case(db):
        _a, _b, rows, tokens = await _world(db)
        async with await _client(db) as client:
            resp = await client.delete(
                f"/api/v1/auth/me/sessions/{rows['cur_a'].id}",
                headers={"Authorization": f"Bearer {tokens['access_a']}"},
            )
        assert resp.status_code == 400
        assert resp.json()["detail"] == (
            "Cannot revoke the current session — use logout instead."
        )
        row = await db.get(AuthSession, rows["cur_a"].id)
        assert row.revoked_at is None

    _run(_case, tmp_path)


def test_revoke_already_revoked_or_expired_is_404(tmp_path):
    async def _case(db):
        _a, _b, rows, tokens = await _world(db)
        async with await _client(db) as client:
            headers = {"Authorization": f"Bearer {tokens['access_a']}"}
            r1 = await client.delete(
                f"/api/v1/auth/me/sessions/{rows['revoked_a'].id}",
                headers=headers,
            )
            r2 = await client.delete(
                f"/api/v1/auth/me/sessions/{rows['expired_a'].id}",
                headers=headers,
            )
        assert r1.status_code == 404
        assert r2.status_code == 404

    _run(_case, tmp_path)


def test_revoke_writes_audit_row(tmp_path):
    async def _case(db):
        user_a, _b, rows, tokens = await _world(db)
        async with await _client(db) as client:
            resp = await client.delete(
                f"/api/v1/auth/me/sessions/{rows['other_a'].id}",
                headers={"Authorization": f"Bearer {tokens['access_a']}"},
            )
        assert resp.status_code == 200
        audits = (
            await db.execute(
                select(AuditLog).where(
                    AuditLog.user_id == user_a.id,
                    AuditLog.action == "SESSION_REVOKED",
                )
            )
        ).scalars().all()
        assert len(audits) == 1
        audit = audits[0]
        assert audit.table_name == "auth_sessions"
        assert audit.new_value == {"reason": "user_revoked_session"}
        assert audit.ip_address is not None

    _run(_case, tmp_path)


def test_revoked_session_access_token_fails_at_once(tmp_path):
    """Design-correction pin: get_current_user validates the session
    row, so a revoked session's access token 401s at once — it does
    NOT stay usable until the 30-minute natural expiry."""
    async def _case(db):
        _a, _b, rows, tokens = await _world(db)
        async with await _client(db) as client:
            headers_a = {"Authorization": f"Bearer {tokens['access_a']}"}
            resp = await client.delete(
                f"/api/v1/auth/me/sessions/{rows['other_a'].id}",
                headers=headers_a,
            )
            assert resp.status_code == 200
            stale = await client.get(
                "/api/v1/auth/me/sessions",
                headers={
                    "Authorization": f"Bearer {tokens['access_other_a']}"
                },
            )
        assert stale.status_code == 401
        assert stale.json()["detail"] == "Session has been revoked or expired."

    _run(_case, tmp_path)


def test_current_marking_follows_requesting_token(tmp_path):
    async def _case(db):
        _a, _b, rows, tokens = await _world(db)
        async with await _client(db) as client:
            resp = await client.get(
                "/api/v1/auth/me/sessions",
                headers={
                    "Authorization": f"Bearer {tokens['access_other_a']}"
                },
            )
        by_id = {s["id"]: s for s in resp.json()["sessions"]}
        assert by_id[str(rows["other_a"].id)]["is_current"] is True
        assert by_id[str(rows["cur_a"].id)]["is_current"] is False

    _run(_case, tmp_path)
