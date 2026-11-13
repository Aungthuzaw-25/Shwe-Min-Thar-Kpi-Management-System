"""Enforce the TOTP code in OAuth login completion for enrolled users (2026-11-07).

Covers: an enrolled SA's OAuth login is interrupted into an MFA
challenge with NO session issued; the correct code completes the
login (session + cookies + dashboard redirect); a wrong code fails
generically and the SAME challenge stays retryable; 5 wrong codes
freeze TOTP for 1 hour; a used challenge cannot mint a second
session; a challenge from another browser fails; an unenrolled SA's
OAuth login is unchanged; Admin/HR still cannot link OAuth.

The TOTP codes here are computed by an INDEPENDENT RFC 6238
implementation — never by the app's own verifier.
"""

import asyncio
import base64
import hashlib
import hmac
import secrets
import struct
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import cache as cache_module
from app.core.config import settings
from app.core.security import get_password_hash
from app.database.base import Base
from app.database.connection import get_db
from app.main import app
from app.modules.auth import router as auth_router
from app.modules.auth.models import AuthSession, SATotpAttempt
from app.modules.auth.oauth_state import create_oauth_state
from app.modules.users.models import OAuthAccount, Role, User

PASSWORD = "Test@1234"
ISSUER = "https://accounts.google.com"


def totp_code(secret: str, at: float | None = None) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    step = int((at if at is not None else time.time()) // 30)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF) % 1000000
    return str(code).zfill(6)


def _wrong_code(secret: str) -> str:
    """A 6-digit code that is wrong for the current step AND its neighbours."""
    valid = {totp_code(secret, at=time.time() + delta) for delta in (-30, 0, 30)}
    code = "000000"
    while code in valid:
        code = str((int(code) + 1) % 1000000).zfill(6)
    return code


class FakeRedis:
    """Cache-module fake (permver counters etc.)."""

    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)


class FakeOAuthRedis:
    """OAuth-state fake: set/get/getdel/delete with the real key shapes."""

    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None, nx=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def getdel(self, key):
        return self.store.pop(key, None)

    async def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)


class FakeProvider:
    """OIDC provider stub: the identity it returns is configurable per test."""

    def __init__(self):
        self.subject = "subject-1"
        self.email = "sa1@example.com"

    async def exchange_code(self, code, verifier):
        return {"id_token": "fake-id-token"}

    async def validate_identity(self, token_response, nonce):
        return SimpleNamespace(
            provider="google",
            issuer=ISSUER,
            tenant_id=None,
            subject=self.subject,
            email=self.email,
        )

    async def validate_callback_issuer(self, issuer):
        return None


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "TOTP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "TOTP_ENCRYPTION_KEY_PREVIOUS", None)
    monkeypatch.setattr(settings, "OAUTH_ENABLED", True)
    monkeypatch.setattr(cache_module, "_client", FakeRedis())
    monkeypatch.setattr(cache_module, "_client_unavailable", False)
    auth_router.limiter.enabled = False
    if hasattr(app.state, "limiter"):
        app.state.limiter.enabled = False

    oauth_redis = FakeOAuthRedis()
    previous_oauth_redis = getattr(app.state, "oauth_redis", None)
    fake_provider = FakeProvider()
    monkeypatch.setattr(auth_router, "_provider", lambda name: fake_provider)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'oauth-mfa.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c))
        async with factory() as db:
            roles = {}
            for name in ("Super Admin", "Admin", "HR", "Department User"):
                role = Role(id=uuid4(), name=name)
                db.add(role)
                roles[name] = role
            await db.flush()
            user_ids = {}
            for key, role_name in (
                ("sa1", "Super Admin"),
                ("sa2", "Super Admin"),
                ("admin", "Admin"),
                ("member", "Department User"),
            ):
                user = User(
                    id=uuid4(),
                    email=f"{key}@example.com",
                    first_name=key,
                    last_name="Test",
                    hashed_password=get_password_hash(PASSWORD),
                    local_password_enabled=True,
                    must_change_password=False,
                    is_active=True,
                    role_id=roles[role_name].id,
                )
                db.add(user)
                user_ids[key] = user.id
            await db.flush()
            # Linked OAuth accounts: sa1 (enrolled in the tests) and
            # sa2 (never enrolled) — subject is the lookup key.
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            for key, subject in (("sa1", "subject-1"), ("sa2", "subject-2")):
                db.add(
                    OAuthAccount(
                        user_id=user_ids[key],
                        provider="google",
                        issuer=ISSUER,
                        tenant_id="",
                        provider_subject=subject,
                        provider_email=f"{key}@example.com",
                        email_verified=True,
                        linked_at=now,
                    )
                )
            await db.commit()
            return {"users": user_ids}

    ids = asyncio.run(_init())

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=False) as test_client:
        # AFTER the lifespan runs: the lifespan overwrites
        # app.state.oauth_redis (None here — no real Redis in the CI
        # env), so the fake must be installed after entry, or every
        # OAuth flow fails closed with a 503.
        app.state.oauth_redis = oauth_redis
        test_client.ids = ids
        test_client.factory = factory
        test_client.oauth_redis = oauth_redis
        test_client.fake_provider = fake_provider
        yield test_client
    app.dependency_overrides.pop(get_db, None)
    app.state.oauth_redis = previous_oauth_redis
    auth_router.limiter.enabled = True
    if hasattr(app.state, "limiter"):
        app.state.limiter.enabled = True


def login(client, email, password=PASSWORD, totp=None):
    payload = {"email": email, "password": password}
    if totp is not None:
        payload["totp_code"] = totp
    return client.post("/api/v1/auth/login", json=payload)


def enroll(client, email):
    """Enroll via the real SA setup/confirm endpoints; returns the secret."""
    resp = login(client, email)
    assert resp.status_code == 200, resp.text
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    setup = client.post("/api/v1/auth/sa-mfa/setup", headers=headers)
    assert setup.status_code == 200, setup.text
    key = setup.json()["manual_key"]
    confirm = client.post(
        "/api/v1/auth/sa-mfa/confirm", json={"code": totp_code(key)}, headers=headers
    )
    assert confirm.status_code == 200, confirm.text
    return key


def oauth_callback(client, subject="subject-1", email="sa1@example.com"):
    """Run the Google callback for the fake provider's identity.

    Seeds a real OAuth state in the fake Redis and sets the matching
    browser-binding cookie, exactly as /google/start would.
    """
    client.fake_provider.subject = subject
    client.fake_provider.email = email
    binding = secrets.token_urlsafe(32)
    signature = auth_router._oauth_binding_signature(binding)
    client.cookies.set("oauth_browser_binding", f"{binding}.{signature}")
    state = asyncio.run(
        create_oauth_state(
            client.oauth_redis,
            provider="google",
            nonce="expected-nonce",
            pkce_verifier="verifier",
            browser_binding=binding,
        )
    )
    return client.get(
        f"/api/v1/auth/google/callback?code=fake-code&state={state}",
        follow_redirects=False,
    )


def _challenge_from(resp):
    location = resp.headers.get("location", "")
    assert location.startswith("/auth/login?oauth_mfa_challenge="), location
    return parse_qs(urlparse(location).query)["oauth_mfa_challenge"][0]


def _sessions_for(client, email):
    async def _run():
        async with client.factory() as db:
            user = (
                await db.execute(select(User).where(User.email == email))
            ).scalars().first()
            rows = (
                await db.execute(
                    select(AuthSession).where(AuthSession.user_id == user.id)
                )
            ).scalars().all()
            return rows

    return asyncio.run(_run())


def _session_count(client, email):
    """Session count as a baseline: enroll() already logs in once
    (password), so the OAuth flow must be measured as a DELTA, not
    an absolute count."""
    return len(_sessions_for(client, email))


def _attempt_row(client, email):
    async def _run():
        async with client.factory() as db:
            user = (
                await db.execute(select(User).where(User.email == email))
            ).scalars().first()
            return (
                await db.execute(
                    select(SATotpAttempt).where(SATotpAttempt.user_id == user.id)
                )
            ).scalars().first()

    return asyncio.run(_run())


def test_enrolled_sa_oauth_login_is_challenged_without_session(client):
    enroll(client, "sa1@example.com")
    baseline = _session_count(client, "sa1@example.com")
    resp = oauth_callback(client)
    assert resp.status_code == 303, resp.text
    _challenge_from(resp)
    # No session: no auth cookies and no NEW AuthSession row.
    assert "access_token" not in resp.cookies
    assert "refresh_token" not in resp.cookies
    assert _session_count(client, "sa1@example.com") == baseline
    # The browser holds nothing usable after the challenged callback:
    # drop the enroll login's cookies and /me must refuse.
    client.cookies.clear()
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 401, me.text


def test_enrolled_sa_oauth_login_completes_with_correct_code(client):
    key = enroll(client, "sa1@example.com")
    baseline = _session_count(client, "sa1@example.com")
    challenge = _challenge_from(oauth_callback(client))
    resp = client.post(
        "/api/v1/auth/oauth/mfa-verify",
        json={"challenge": challenge, "totp_code": totp_code(key)},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"redirect_to": "/superadmin/dashboard"}
    assert "access_token" in resp.cookies
    assert "refresh_token" in resp.cookies
    # Single-use: the challenge is gone after success.
    assert f"oauth:mfa_challenge:{challenge}" not in client.oauth_redis.store
    assert _session_count(client, "sa1@example.com") == baseline + 1
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text


def test_wrong_code_fails_generically_and_challenge_stays_retryable(client):
    key = enroll(client, "sa1@example.com")
    challenge = _challenge_from(oauth_callback(client))
    wrong = client.post(
        "/api/v1/auth/oauth/mfa-verify",
        json={"challenge": challenge, "totp_code": _wrong_code(key)},
    )
    assert wrong.status_code == 401, wrong.text
    assert wrong.json()["detail"]["code"] == "MFA_INVALID"
    # The freeze counter moved — and the SAME challenge still works.
    row = _attempt_row(client, "sa1@example.com")
    assert row is not None and row.failed_attempts == 1
    good = client.post(
        "/api/v1/auth/oauth/mfa-verify",
        json={"challenge": challenge, "totp_code": totp_code(key)},
    )
    assert good.status_code == 200, good.text
    assert good.json() == {"redirect_to": "/superadmin/dashboard"}


def test_five_wrong_codes_freeze_totp_for_an_hour(client):
    key = enroll(client, "sa1@example.com")
    challenge = _challenge_from(oauth_callback(client))
    for _ in range(5):
        resp = client.post(
            "/api/v1/auth/oauth/mfa-verify",
            json={"challenge": challenge, "totp_code": _wrong_code(key)},
        )
        assert resp.status_code == 401, resp.text
        assert resp.json()["detail"]["code"] == "MFA_INVALID"
    row = _attempt_row(client, "sa1@example.com")
    assert row is not None and row.failed_attempts == 5
    assert row.frozen_until is not None
    # Even the correct code is refused while frozen.
    frozen = client.post(
        "/api/v1/auth/oauth/mfa-verify",
        json={"challenge": challenge, "totp_code": totp_code(key)},
    )
    assert frozen.status_code == 401, frozen.text
    assert frozen.json()["detail"]["code"] == "MFA_INVALID"


def test_used_challenge_cannot_mint_a_second_session(client):
    key = enroll(client, "sa1@example.com")
    baseline = _session_count(client, "sa1@example.com")
    challenge = _challenge_from(oauth_callback(client))
    first = client.post(
        "/api/v1/auth/oauth/mfa-verify",
        json={"challenge": challenge, "totp_code": totp_code(key)},
    )
    assert first.status_code == 200, first.text
    replay = client.post(
        "/api/v1/auth/oauth/mfa-verify",
        json={"challenge": challenge, "totp_code": totp_code(key)},
    )
    assert replay.status_code == 400, replay.text
    assert _session_count(client, "sa1@example.com") == baseline + 1


def test_challenge_from_another_browser_fails(client):
    key = enroll(client, "sa1@example.com")
    baseline = _session_count(client, "sa1@example.com")
    challenge = _challenge_from(oauth_callback(client))
    client.cookies.clear()
    resp = client.post(
        "/api/v1/auth/oauth/mfa-verify",
        json={"challenge": challenge, "totp_code": totp_code(key)},
    )
    assert resp.status_code == 400, resp.text
    assert _session_count(client, "sa1@example.com") == baseline


def test_unenrolled_sa_oauth_login_unchanged(client):
    resp = oauth_callback(client, subject="subject-2", email="sa2@example.com")
    assert resp.status_code == 303, resp.text
    assert resp.headers.get("location") == "/superadmin/dashboard"
    assert "access_token" in resp.cookies
    assert len(_sessions_for(client, "sa2@example.com")) == 1
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200, me.text


def test_admin_cannot_link_oauth(client):
    resp = login(client, "admin@example.com")
    assert resp.status_code == 200, resp.text
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    link = client.get("/api/v1/auth/google/link/start", headers=headers)
    assert link.status_code == 403, link.text
