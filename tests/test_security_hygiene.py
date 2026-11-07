"""Security hygiene batch (2026-11-06, Cyber audit lows + 1 medium).

1. Reset-to-Role-Defaults route shadow: DELETE
   /user/{id}/all-overrides was swallowed by the earlier-registered
   DELETE /user/{id}/{permission_code} (permission_code="all-overrides"
   matches nothing, so the reset silently did nothing and returned
   204). The all-overrides route now registers first.
2. Public /health no longer leaks version/environment.
3. Refresh-token reuse revokes the whole session + audits it.
7. CORS allow_headers restricted to the headers the app sends.
8. revoke_user_authentication uses ORM updates (raw text() with
   str(uuid) params silently matches zero rows on SQLite).

Fixes 4 (CI Node pin), 5 (requirements pins) and 6 (asteval removal)
are config-only: they are verified by the full suites + a pip
dry-run, not by unit tests.
"""

import asyncio
import fnmatch
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

import app.core.cache as cache_module
from app.core import security
from app.core.security import get_password_hash
from app.database.base import Base
from app.database.connection import get_db
import app.main as main_module
from app.models.audit_log import AuditLog
from app.modules.auth import router as auth_router
from app.modules.auth.models import AuthSession
from app.modules.departments.models import Department, UserDepartment
from app.modules.users.models import (
    OAuthAccount,
    Permission,
    Role,
    RolePermission,
    User,
    UserPermissionOverride,
)
from app.modules.users.router import revoke_user_authentication

PASSWORD = "StrongPass!123"


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def setex(self, key, ttl, value):
        self.store[key] = value

    async def incr(self, key):
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]

    async def delete(self, *keys):
        removed = 0
        for key in keys:
            if self.store.pop(key, None) is not None:
                removed += 1
        return removed

    async def scan_iter(self, match=None, count=None):
        # Unlike the phase2 fake, this one honours the glob: the
        # dashboard purge after a DELETE must not wipe unrelated
        # keys (e.g. the permver counter this batch asserts on).
        for key in list(self.store):
            if match is None or fnmatch.fnmatch(key, match):
                yield key

    async def ping(self):
        return True


# ---------------------------------------------------------------------------
# Fix 1 — permission routes world (sync engine, real JWTs)
# ---------------------------------------------------------------------------


@pytest.fixture()
def perm_world(tmp_path, monkeypatch):
    db_file = tmp_path / "hygiene-perm.db"
    sync_engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(sync_engine)
    session = sessionmaker(sync_engine)()
    perms = {}
    for code in ("users.view", "users.edit", "system.permissions.manage"):
        perm = Permission(code=code, description=code)
        session.add(perm)
        session.flush()
        perms[code] = perm

    sa_role = Role(name="Super Admin")
    member_role = Role(name="Department User")
    session.add_all([sa_role, member_role])
    session.flush()

    def make_user(email, role):
        user = User(
            email=email,
            hashed_password=get_password_hash(PASSWORD),
            first_name="T",
            last_name=email.split("@")[0],
            role_id=role.id,
            is_active=True,
        )
        session.add(user)
        session.flush()
        auth_session = AuthSession(
            user_id=user.id,
            refresh_token_hash=uuid4().hex + uuid4().hex,
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=1),
            last_used_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
        session.add(auth_session)
        session.flush()
        token = security.create_access_token(
            {"sub": email, "user_id": str(user.id), "sid": str(auth_session.id)}
        )
        return user, token

    sa, sa_token = make_user("sa@example.com", sa_role)
    member, _member_token = make_user("member@example.com", member_role)
    dept = Department(name="Alpha")
    session.add(dept)
    session.flush()
    session.add(UserDepartment(user_id=member.id, department_id=dept.id, user_level="L2"))
    for code in ("users.view", "users.edit"):
        session.add(UserPermissionOverride(
            user_id=member.id, permission_id=perms[code].id,
            allowed=False, scope="ALL", department_id=None,
        ))
    session.commit()
    ids = SimpleNamespace(sa=sa.id, member=member.id)
    session.close()

    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    async def override_get_db():
        async with factory() as db_session:
            yield db_session

    main_module.app.dependency_overrides[get_db] = override_get_db
    fake = FakeRedis()
    monkeypatch.setattr(cache_module, "_client", fake)
    monkeypatch.setattr(cache_module, "_client_unavailable", False)
    client = TestClient(main_module.app, raise_server_exceptions=False)
    yield SimpleNamespace(client=client, ids=ids, sa_token=sa_token, fake=fake, factory=factory)
    main_module.app.dependency_overrides.pop(get_db, None)


def _overrides(client, user_id, token):
    resp = client.get(
        f"/api/v1/permissions/user/{user_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_reset_to_role_defaults_actually_deletes_and_bumps_version(perm_world):
    w = perm_world
    headers = {"Authorization": f"Bearer {w.sa_token}"}
    assert len(_overrides(w.client, w.ids.member, w.sa_token)) == 2
    assert w.fake.store.get(f"permver:user:{w.ids.member}") in (None, 0)

    # The old bug answered 204 here (the shadow route) and deleted
    # nothing. The real reset answers 200 with a count.
    resp = w.client.delete(
        f"/api/v1/permissions/user/{w.ids.member}/all-overrides", headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] == 2
    assert _overrides(w.client, w.ids.member, w.sa_token) == []
    assert w.fake.store.get(f"permver:user:{w.ids.member}") == 1


def test_single_override_delete_still_works_after_reorder(perm_world):
    w = perm_world
    headers = {"Authorization": f"Bearer {w.sa_token}"}
    resp = w.client.delete(
        f"/api/v1/permissions/user/{w.ids.member}/users.view", headers=headers
    )
    assert resp.status_code == 204, resp.text
    remaining = _overrides(w.client, w.ids.member, w.sa_token)
    assert [row["permission_code"] for row in remaining] == ["users.edit"]


# ---------------------------------------------------------------------------
# Fixes 2 + 7 — public surface (no DB world needed)
# ---------------------------------------------------------------------------


def test_public_health_hides_version_and_environment():
    with TestClient(main_module.app) as client:
        resp = client.get("/health")
    # The DB may be up or down in this environment (200 vs 503);
    # either way the body must not carry deployment facts.
    body = resp.json()
    assert "status" in body
    assert "version" not in body
    assert "environment" not in body
    assert set(body.keys()) == {"status", "project"}


def test_cors_preflight_allows_only_the_headers_the_app_sends():
    with TestClient(main_module.app) as client:
        allowed = client.options(
            "/api/v1/health",
            headers={
                "Origin": "http://localhost:8000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": (
                    "Authorization, Content-Type, X-CSRF-Token, "
                    "Accept, Idempotency-Key"
                ),
            },
        )
        assert allowed.status_code == 200
        allow_headers = allowed.headers.get("access-control-allow-headers", "").lower()
        for header in (
            "authorization",
            "content-type",
            "x-csrf-token",
            "accept",
            "idempotency-key",
        ):
            assert header in allow_headers

        denied = client.options(
            "/api/v1/health",
            headers={
                "Origin": "http://localhost:8000",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-Custom-Header",
            },
        )
        assert denied.status_code == 400
        assert "x-custom-header" not in denied.headers.get(
            "access-control-allow-headers", ""
        ).lower()


# ---------------------------------------------------------------------------
# Fixes 3 + 8 — auth world (real login + real sessions)
# ---------------------------------------------------------------------------


@pytest.fixture()
def auth_world(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "_client", FakeRedis())
    monkeypatch.setattr(cache_module, "_client_unavailable", False)
    auth_router.limiter.enabled = False
    if hasattr(main_module.app.state, "limiter"):
        main_module.app.state.limiter.enabled = False

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'hygiene-auth.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c))
        async with factory() as db:
            sa_role = Role(id=uuid4(), name="Super Admin")
            db.add(sa_role)
            await db.flush()
            user = User(
                id=uuid4(),
                email="sa@example.com",
                first_name="sa",
                last_name="Test",
                hashed_password=get_password_hash(PASSWORD),
                local_password_enabled=True,
                must_change_password=False,
                is_active=True,
                role_id=sa_role.id,
            )
            db.add(user)
            await db.commit()
            return user.id

    user_id = asyncio.run(_init())

    async def override_get_db():
        async with factory() as session:
            yield session

    main_module.app.dependency_overrides[get_db] = override_get_db
    with TestClient(main_module.app, raise_server_exceptions=False) as test_client:
        test_client.factory = factory
        test_client.user_id = user_id
        yield test_client
    main_module.app.dependency_overrides.pop(get_db, None)
    auth_router.limiter.enabled = True
    if hasattr(main_module.app.state, "limiter"):
        main_module.app.state.limiter.enabled = True


def _login(client):
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "sa@example.com", "password": PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    # The login sets HttpOnly cookies; a later body-token refresh
    # with those cookies present trips CSRF (cookie-authenticated
    # POST, no X-CSRF-Token). The body tokens are what this test
    # uses, so drop the cookies.
    client.cookies.clear()
    return resp.json()


def _refresh(client, refresh_token):
    # Each refresh sets fresh HttpOnly cookies; a replay with those
    # cookies present would 403 on CSRF before the endpoint runs.
    # This test drives the body-token form, so drop the cookies.
    client.cookies.clear()
    return client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})


def test_refresh_token_reuse_revokes_session_and_audits(auth_world):
    client = auth_world
    tokens = _login(client)
    rotated = _refresh(client, tokens["refresh_token"])
    assert rotated.status_code == 200, rotated.text
    new_tokens = rotated.json()
    assert new_tokens["refresh_token"] != tokens["refresh_token"]

    # Replay the old (already rotated) refresh token: 401, and the
    # whole session dies — not just the one token.
    replay = _refresh(client, tokens["refresh_token"])
    assert replay.status_code == 401

    async def _check():
        async with client.factory() as db:
            sessions = (await db.execute(
                select(AuthSession).where(AuthSession.user_id == client.user_id)
            )).scalars().all()
            assert len(sessions) == 1
            assert sessions[0].revoked_at is not None
            audits = (await db.execute(
                select(AuditLog).where(
                    AuditLog.action == "SESSION_REVOKED",
                    AuditLog.user_id == client.user_id,
                )
            )).scalars().all()
            assert len(audits) == 1
            assert audits[0].new_value.get("reason") == "refresh_token_reuse"

    asyncio.run(_check())

    # The rotated token is dead too (its session is gone), and the
    # access token from that rotation 401s at once.
    assert _refresh(client, new_tokens["refresh_token"]).status_code == 401
    me = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {new_tokens['access_token']}"},
    )
    assert me.status_code == 401


def test_revoke_user_authentication_revokes_sessions_and_oauth(auth_world):
    client = auth_world
    other_id = uuid4()
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    async def _seed():
        async with client.factory() as db:
            other = User(
                id=other_id,
                email="other@example.com",
                first_name="o",
                last_name="Test",
                hashed_password=get_password_hash(PASSWORD),
                local_password_enabled=True,
                must_change_password=False,
                is_active=True,
                role_id=(await db.execute(select(Role))).scalars().first().id,
            )
            db.add(other)
            await db.flush()
            target_session = AuthSession(
                user_id=client.user_id,
                refresh_token_hash="aaa",
                expires_at=now + timedelta(days=1),
                last_used_at=now,
            )
            other_session = AuthSession(
                user_id=other_id,
                refresh_token_hash="bbb",
                expires_at=now + timedelta(days=1),
                last_used_at=now,
            )
            oauth = OAuthAccount(
                user_id=client.user_id,
                provider="google",
                issuer="https://accounts.google.com",
                tenant_id="",
                provider_subject="sub-1",
                linked_at=now,
            )
            db.add_all([target_session, other_session, oauth])
            await db.commit()
            return target_session.id, other_session.id, oauth.id

    target_sid, other_sid, oauth_id = asyncio.run(_seed())

    async def _run():
        async with client.factory() as db:
            await revoke_user_authentication(db, client.user_id)
            await db.commit()

    asyncio.run(_run())

    async def _assert():
        async with client.factory() as db:
            target = (await db.execute(
                select(AuthSession).where(AuthSession.id == target_sid)
            )).scalars().first()
            other = (await db.execute(
                select(AuthSession).where(AuthSession.id == other_sid)
            )).scalars().first()
            account = (await db.execute(
                select(OAuthAccount).where(OAuthAccount.id == oauth_id)
            )).scalars().first()
            assert target.revoked_at is not None
            assert account.revoked_at is not None
            assert other.revoked_at is None

    asyncio.run(_assert())
