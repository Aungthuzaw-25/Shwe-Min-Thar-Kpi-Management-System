"""SA password lockout trap (2026-11-13, Zenn found it live).

An SA who never enrolled in Authenticator and forgets the password
had no way back: break-glass needs the CURRENT password, recover
needs a TOTP code, and peer reset needs another SA to act. Two
fixes, same branch:

1. ``POST /sa-mfa/recover-available`` — the login page asks (with
   the typed email) whether "Recover with Authenticator" is even
   meaningful. True only for an enrolled SA; nonexistent / non-SA /
   unenrolled all answer False alike.
2. ``POST /sa-mfa/break-glass-reset-password`` — email + owner
   break-glass token + new password, no current password, no TOTP,
   for an UNENROLLED SA only. Enrolled SAs are refused (they use
   recover); the token can never bypass an enrolled account's TOTP.

The TOTP codes here are computed by an INDEPENDENT RFC 6238
implementation — never by the app's own verifier.
"""

import asyncio
import base64
import hashlib
import hmac
import struct
import time
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
from app.models.audit_log import AuditLog
from app.modules.auth import router as auth_router
from app.modules.users.models import Role, User

PASSWORD = "StrongPass!123"
NEW_PASSWORD = "BrandNewPass!456"
SECOND_PASSWORD = "SecondPass!456"
BREAK_GLASS_TOKEN = "break-glass-test-token-that-is-long-enough-0123456789"
GENERIC_BG_FAILURE = "Invalid email, break-glass token, or account state."


def totp_code(secret: str, at: float | None = None) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    step = int((at if at is not None else time.time()) // 30)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF) % 1000000
    return str(code).zfill(6)


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    key_a = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "TOTP_ENCRYPTION_KEY", key_a)
    monkeypatch.setattr(settings, "TOTP_ENCRYPTION_KEY_PREVIOUS", None)
    monkeypatch.setattr(settings, "SA_MFA_BREAK_GLASS_TOKEN", BREAK_GLASS_TOKEN)
    monkeypatch.setattr(cache_module, "_client", FakeRedis())
    monkeypatch.setattr(cache_module, "_client_unavailable", False)
    auth_router.limiter.enabled = False
    if hasattr(app.state, "limiter"):
        app.state.limiter.enabled = False

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sa-lockout.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c))
        async with factory() as db:
            sa_role = Role(id=uuid4(), name="Super Admin")
            admin_role = Role(id=uuid4(), name="Admin")
            db.add_all([sa_role, admin_role])
            await db.flush()
            for key, role in (("sa1", sa_role), ("sa2", sa_role), ("admin", admin_role)):
                db.add(User(
                    id=uuid4(),
                    email=f"{key}@example.com",
                    first_name=key,
                    last_name="Test",
                    hashed_password=get_password_hash(PASSWORD),
                    local_password_enabled=True,
                    must_change_password=False,
                    is_active=True,
                    role_id=role.id,
                ))
            await db.commit()

    asyncio.run(_init())

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=False) as test_client:
        test_client.factory = factory
        yield test_client
    app.dependency_overrides.pop(get_db, None)
    auth_router.limiter.enabled = True
    if hasattr(app.state, "limiter"):
        app.state.limiter.enabled = True


def login(client, email, password=PASSWORD, totp=None):
    payload = {"email": email, "password": password}
    if totp is not None:
        payload["totp_code"] = totp
    return client.post("/api/v1/auth/login", json=payload)


def auth_header(client, email, password=PASSWORD, totp=None):
    resp = login(client, email, password, totp)
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def enroll(client, email):
    headers = auth_header(client, email)
    setup = client.post("/api/v1/auth/sa-mfa/setup", headers=headers)
    assert setup.status_code == 200, setup.text
    key = setup.json()["manual_key"]
    confirm = client.post(
        "/api/v1/auth/sa-mfa/confirm", json={"code": totp_code(key)}, headers=headers
    )
    assert confirm.status_code == 200, confirm.text
    return key


def _wait_for_fresh_window(deadline):
    now = time.time()
    assert now < deadline, "fresh code never arrived"
    time.sleep(min(30 - (now % 30) + 0.2, deadline - now))


def _login_with_fresh_code(client, email, key, password=PASSWORD):
    deadline = time.time() + 35
    while True:
        resp = login(client, email, password=password, totp=totp_code(key))
        if resp.status_code == 200:
            return resp
        assert resp.status_code == 401, resp.text
        _wait_for_fresh_window(deadline)


def _recover_available(client, email):
    return client.post("/api/v1/auth/sa-mfa/recover-available", json={"email": email})


def _bg_reset(client, email, token=BREAK_GLASS_TOKEN, new_password=NEW_PASSWORD, confirm=True):
    payload = {"email": email, "break_glass_token": token, "new_password": new_password}
    if confirm:
        payload["confirm_new_password"] = new_password
    return client.post("/api/v1/auth/sa-mfa/break-glass-reset-password", json=payload)


def _audit_rows(client, action):
    async def _run():
        async with client.factory() as db:
            return (await db.execute(
                select(AuditLog).where(AuditLog.action == action)
            )).scalars().all()

    return asyncio.run(_run())


def _user_row(client, email):
    async def _run():
        async with client.factory() as db:
            return (await db.execute(
                select(User).where(User.email == email)
            )).scalars().first()

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# Part 1 — recover-available gating
# ---------------------------------------------------------------------------


def test_recover_available_true_only_for_enrolled_sa(client):
    enroll(client, "sa1@example.com")
    resp = _recover_available(client, "sa1@example.com")
    assert resp.status_code == 200
    assert resp.json() == {"recover_available": True}
    # Unenrolled SA, non-SA, and a made-up email all answer False
    # alike — same shape, no detail, no oracle between them.
    for email in ("sa2@example.com", "admin@example.com", "nobody@example.com"):
        resp = _recover_available(client, email)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"recover_available": False}


def test_recover_available_ignores_password_lockout(client):
    enroll(client, "sa1@example.com")
    for _ in range(4):
        assert login(client, "sa1@example.com", password="WrongPass!999").status_code == 401
    # The 5th wrong password already answers 403: the lock engages
    # on the failing attempt itself, not on the next one.
    locked = login(client, "sa1@example.com", password="WrongPass!999")
    assert locked.status_code == 403
    assert locked.json()["detail"]["code"] == "ACCOUNT_LOCKED"
    assert login(client, "sa1@example.com").status_code == 403
    # The link stays offered — recovery is what clears the lockout.
    assert _recover_available(client, "sa1@example.com").json() == {"recover_available": True}


def test_recover_available_rate_limited(client):
    auth_router.limiter.enabled = True
    try:
        for _ in range(5):
            assert _recover_available(client, "sa1@example.com").status_code == 200
        assert _recover_available(client, "sa1@example.com").status_code == 429
    finally:
        auth_router.limiter.enabled = False


# ---------------------------------------------------------------------------
# Part 2 — break-glass password reset
# ---------------------------------------------------------------------------


def test_break_glass_password_reset_recovers_unenrolled_sa(client):
    resp = _bg_reset(client, "sa1@example.com")
    assert resp.status_code == 200, resp.text
    assert "Rotate the break-glass token" in resp.json()["message"]

    # New password works (password-only — unenrolled), old one does not.
    assert login(client, "sa1@example.com", password=NEW_PASSWORD).status_code == 200
    assert login(client, "sa1@example.com", password=PASSWORD).status_code == 401

    rows = _audit_rows(client, "SA_BREAK_GLASS_PASSWORD_RESET")
    assert len(rows) == 1
    assert rows[0].new_value.get("via") == "break-glass-password-reset"
    assert rows[0].new_value.get("sessions_revoked") == 0
    user = _user_row(client, "sa1@example.com")
    assert user.must_change_password is False
    assert user.password_changed_at is not None
    assert user.is_locked is False


def test_break_glass_password_reset_clears_password_lockout(client):
    for _ in range(4):
        assert login(client, "sa1@example.com", password="WrongPass!999").status_code == 401
    locked = login(client, "sa1@example.com", password="WrongPass!999")
    assert locked.status_code == 403
    assert locked.json()["detail"]["code"] == "ACCOUNT_LOCKED"
    assert _bg_reset(client, "sa1@example.com").status_code == 200
    assert login(client, "sa1@example.com", password=NEW_PASSWORD).status_code == 200


def test_break_glass_password_reset_refuses_enrolled_sa(client):
    key = enroll(client, "sa1@example.com")
    resp = _bg_reset(client, "sa1@example.com")
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"] == GENERIC_BG_FAILURE
    # Password untouched and MFA still on: old password + a fresh
    # code still logs in; no code is still refused.
    assert _login_with_fresh_code(client, "sa1@example.com", key).status_code == 200
    assert login(client, "sa1@example.com").json()["detail"]["code"] == "MFA_REQUIRED"
    assert _audit_rows(client, "SA_BREAK_GLASS_PASSWORD_RESET") == []


def test_break_glass_password_reset_failures_are_generic(client):
    base = {"email": "sa1@example.com", "break_glass_token": BREAK_GLASS_TOKEN}
    wrong_token = _bg_reset(client, "sa1@example.com", token="wrong-token-that-is-long-enough-000")
    assert wrong_token.status_code == 401
    assert wrong_token.json()["detail"] == GENERIC_BG_FAILURE
    unknown = _bg_reset(client, "nobody@example.com")
    assert unknown.status_code == 401
    assert unknown.json()["detail"] == GENERIC_BG_FAILURE
    non_sa = _bg_reset(client, "admin@example.com")
    assert non_sa.status_code == 401
    assert non_sa.json()["detail"] == GENERIC_BG_FAILURE
    # Weak password is a client error about the NEW password (checked
    # before any account lookup, so it leaks nothing).
    weak = _bg_reset(client, "sa1@example.com", new_password="weak")
    assert weak.status_code == 400
    # Confirmation mismatch is the same kind of error.
    resp = client.post(
        "/api/v1/auth/sa-mfa/break-glass-reset-password",
        json={**base, "new_password": NEW_PASSWORD, "confirm_new_password": "OtherPass!999"},
    )
    assert resp.status_code == 400
    # History: the second reset to the same password is refused.
    assert _bg_reset(client, "sa1@example.com", new_password=SECOND_PASSWORD).status_code == 200
    again = _bg_reset(client, "sa1@example.com", new_password=SECOND_PASSWORD)
    assert again.status_code == 400
    assert "last 5 passwords" in again.json()["detail"]


def test_break_glass_password_reset_unconfigured_503(client, monkeypatch):
    monkeypatch.setattr(settings, "SA_MFA_BREAK_GLASS_TOKEN", None)
    assert _bg_reset(client, "sa1@example.com").status_code == 503


def test_break_glass_password_reset_rate_limited(client):
    auth_router.limiter.enabled = True
    try:
        for _ in range(5):
            assert _bg_reset(
                client, "sa1@example.com", token="wrong-token-that-is-long-enough-000"
            ).status_code == 401
        assert _bg_reset(
            client, "sa1@example.com", token="wrong-token-that-is-long-enough-000"
        ).status_code == 429
    finally:
        auth_router.limiter.enabled = False
