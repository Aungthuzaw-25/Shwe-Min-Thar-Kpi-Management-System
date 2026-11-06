"""TOTP key rotation + break-glass (2026-11-06).

Covers: rotation keeps old-key secrets working (dual-key decrypt)
and moves them to the new key on login (lazy re-encryption); new
enrollments use the new key; key-status counts + fingerprints;
key loss deadlocks login + recover (both 503) and break-glass
(token + password, audited) recovers the SA; wrong token/password
and unknown emails all fail generically; break-glass is rate-limited;
an invalid previous key fails closed.

The TOTP codes here are computed by an INDEPENDENT RFC 6238
implementation — never by the app's own verifier.
"""

import asyncio
import base64
import hashlib
import hmac
import struct
import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet, InvalidToken
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
from app.modules.auth.models import SATotpSecret
from app.modules.users.models import Role, User

PASSWORD = "StrongPass!123"
BREAK_GLASS_TOKEN = "break-glass-test-token-that-is-long-enough-0123456789"


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

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sa-rot.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c))
        async with factory() as db:
            sa_role = Role(id=uuid4(), name="Super Admin")
            admin_role = Role(id=uuid4(), name="Admin")
            db.add_all([sa_role, admin_role])
            await db.flush()
            user_ids = {}
            for key, role in (("sa1", sa_role), ("sa2", sa_role), ("admin", admin_role)):
                user = User(
                    id=uuid4(),
                    email=f"{key}@example.com",
                    first_name=key,
                    last_name="Test",
                    hashed_password=get_password_hash(PASSWORD),
                    local_password_enabled=True,
                    must_change_password=False,
                    is_active=True,
                    role_id=role.id,
                )
                db.add(user)
                user_ids[key] = user.id
            await db.commit()
            return {"users": user_ids}

    ids = asyncio.run(_init())

    async def override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app, raise_server_exceptions=False) as test_client:
        test_client.ids = ids
        test_client.factory = factory
        test_client.key_a = key_a
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


def _secret_ciphertext(client, email):
    async def _run():
        async with client.factory() as db:
            user = (await db.execute(select(User).where(User.email == email))).scalars().first()
            row = (await db.execute(
                select(SATotpSecret).where(SATotpSecret.user_id == user.id)
            )).scalars().first()
            return row.secret_encrypted if row else None

    return asyncio.run(_run())


def _key_status(client, email):
    """Read key-status as an UNENROLLED SA (password-only login).

    Reading as the enrolled SA would log them in — and a login
    migrates a previous-key secret, which would move the very counts
    this helper is about to measure.
    """
    headers = auth_header(client, email)
    resp = client.get("/api/v1/auth/sa-mfa/key-status", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _rotate(client, monkeypatch, key_a):
    key_b = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "TOTP_ENCRYPTION_KEY_PREVIOUS", key_a)
    monkeypatch.setattr(settings, "TOTP_ENCRYPTION_KEY", key_b)
    return key_b


def test_rotation_old_secret_still_logs_in_and_moves_to_new_key(client, monkeypatch):
    manual_key = enroll(client, "sa1@example.com")
    key_b = _rotate(client, monkeypatch, client.key_a)

    resp = _login_with_fresh_code(client, "sa1@example.com", manual_key)
    assert resp.status_code == 200

    # The stored secret moved to the new key on that login.
    ciphertext = _secret_ciphertext(client, "sa1@example.com")
    assert Fernet(key_b).decrypt(ciphertext.encode())
    with pytest.raises(InvalidToken):
        Fernet(client.key_a).decrypt(ciphertext.encode())

    # Rotation finished: drop the previous key; the migrated secret
    # still logs in without the old key anywhere.
    monkeypatch.setattr(settings, "TOTP_ENCRYPTION_KEY_PREVIOUS", None)
    resp = _login_with_fresh_code(client, "sa1@example.com", manual_key)
    assert resp.status_code == 200


def test_new_enrollments_use_new_key(client, monkeypatch):
    enroll(client, "sa1@example.com")
    key_b = _rotate(client, monkeypatch, client.key_a)
    enroll(client, "sa2@example.com")

    ciphertext = _secret_ciphertext(client, "sa2@example.com")
    assert Fernet(key_b).decrypt(ciphertext.encode())
    with pytest.raises(InvalidToken):
        Fernet(client.key_a).decrypt(ciphertext.encode())


def test_key_status_counts_and_fingerprints(client, monkeypatch):
    enroll(client, "sa1@example.com")

    # Read as sa2 (unenrolled): reading as sa1 would log sa1 in and
    # migrate the secret being counted.
    status = _key_status(client, "sa2@example.com")
    assert status["secrets_total"] == 1
    assert status["secrets_on_current_key"] == 1
    assert status["secrets_on_previous_key"] == 0
    assert status["secrets_undecryptable"] == 0
    assert status["previous_key_configured"] is False
    assert status["break_glass_configured"] is True
    assert status["rotation_complete"] is True
    assert status["current_key_fingerprint"] == hashlib.sha256(
        client.key_a.encode()
    ).hexdigest()[:12]

    key_b = _rotate(client, monkeypatch, client.key_a)
    status = _key_status(client, "sa2@example.com")
    assert status["previous_key_configured"] is True
    assert status["secrets_on_previous_key"] == 1
    assert status["secrets_on_current_key"] == 0
    assert status["rotation_complete"] is False
    assert status["current_key_fingerprint"] == hashlib.sha256(
        key_b.encode()
    ).hexdigest()[:12]

    # One login migrates the secret; rotation is complete — covered
    # by test_key_status_after_login_shows_rotation_complete.

    # Admins may not read key status.
    headers = auth_header(client, "admin@example.com")
    assert client.get("/api/v1/auth/sa-mfa/key-status", headers=headers).status_code == 403


def test_key_status_after_login_shows_rotation_complete(client, monkeypatch):
    manual_key = enroll(client, "sa1@example.com")
    _rotate(client, monkeypatch, client.key_a)
    _login_with_fresh_code(client, "sa1@example.com", manual_key)
    status = _key_status(client, "sa2@example.com")
    assert status["secrets_on_previous_key"] == 0
    assert status["secrets_on_current_key"] == 1
    assert status["rotation_complete"] is True


def test_key_loss_deadlock_then_break_glass_recovers(client, monkeypatch):
    manual_key = enroll(client, "sa1@example.com")
    # Simulate the lost key: a brand-new key, previous unset.
    key_c = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "TOTP_ENCRYPTION_KEY", key_c)
    monkeypatch.setattr(settings, "TOTP_ENCRYPTION_KEY_PREVIOUS", None)

    # The deadlock: login and recover both 503, peer reset needs a
    # session that needs TOTP.
    assert login(client, "sa1@example.com", totp=totp_code(manual_key)).status_code == 503
    recover = client.post(
        "/api/v1/auth/sa-mfa/recover",
        json={"email": "sa1@example.com", "code": totp_code(manual_key)},
    )
    assert recover.status_code == 503
    # ...but key-status still diagnoses it (SA session from before
    # the loss is not needed — use a fresh SA login? sa2 is not
    # enrolled, so it logs in password-only and can read status).
    status = _key_status(client, "sa2@example.com")
    assert status["secrets_undecryptable"] == 1

    # Break-glass: owner token + the SA's own password.
    resp = client.post(
        "/api/v1/auth/sa-mfa/break-glass",
        json={
            "email": "sa1@example.com",
            "password": PASSWORD,
            "break_glass_token": BREAK_GLASS_TOKEN,
        },
    )
    assert resp.status_code == 200, resp.text

    # MFA is off: password-only login works, and re-enrollment works
    # under the new key.
    assert login(client, "sa1@example.com").status_code == 200
    new_key = enroll(client, "sa1@example.com")
    ciphertext = _secret_ciphertext(client, "sa1@example.com")
    assert Fernet(key_c).decrypt(ciphertext.encode())

    async def _audit():
        async with client.factory() as db:
            rows = (await db.execute(
                select(AuditLog).where(AuditLog.action == "SA_MFA_BREAK_GLASS_RESET")
            )).scalars().all()
            return rows

    rows = asyncio.run(_audit())
    assert len(rows) == 1
    assert rows[0].new_value.get("via") == "break-glass"


def test_break_glass_rejects_wrong_token_password_and_unknown(client, monkeypatch):
    enroll(client, "sa1@example.com")
    base = {
        "email": "sa1@example.com",
        "password": PASSWORD,
        "break_glass_token": BREAK_GLASS_TOKEN,
    }
    generic = "Invalid email, password, or break-glass token."
    wrong_token = dict(base, break_glass_token="wrong-token-that-is-long-enough-000")
    assert client.post("/api/v1/auth/sa-mfa/break-glass", json=wrong_token).status_code == 401
    assert client.post("/api/v1/auth/sa-mfa/break-glass", json=wrong_token).json()["detail"] == generic
    wrong_password = dict(base, password="WrongPass!999")
    resp = client.post("/api/v1/auth/sa-mfa/break-glass", json=wrong_password)
    assert resp.status_code == 401
    assert resp.json()["detail"] == generic
    unknown = dict(base, email="nobody@example.com")
    resp = client.post("/api/v1/auth/sa-mfa/break-glass", json=unknown)
    assert resp.status_code == 401
    assert resp.json()["detail"] == generic
    # An SA with no MFA enrolled has nothing to break — same generic 401.
    not_enrolled = dict(base, email="sa2@example.com")
    assert client.post("/api/v1/auth/sa-mfa/break-glass", json=not_enrolled).status_code == 401

    # No token configured at all: fail closed (503), never a bypass.
    monkeypatch.setattr(settings, "SA_MFA_BREAK_GLASS_TOKEN", None)
    resp = client.post("/api/v1/auth/sa-mfa/break-glass", json=base)
    assert resp.status_code == 503


def test_break_glass_rate_limited(client):
    enroll(client, "sa1@example.com")
    # The auth limiter keys by client address (TestClient "testclient");
    # it was disabled for every other test in this file, so this
    # test's 5 attempts are the first counts in the bucket.
    auth_router.limiter.enabled = True
    try:
        payload = {
            "email": "sa1@example.com",
            "password": "WrongPass!999",
            "break_glass_token": BREAK_GLASS_TOKEN,
        }
        for _ in range(5):
            assert client.post(
                "/api/v1/auth/sa-mfa/break-glass", json=payload
            ).status_code == 401
        assert client.post(
            "/api/v1/auth/sa-mfa/break-glass", json=payload
        ).status_code == 429
    finally:
        auth_router.limiter.enabled = False


def test_rotation_with_invalid_previous_key_fails_closed(client, monkeypatch):
    monkeypatch.setattr(settings, "TOTP_ENCRYPTION_KEY_PREVIOUS", "not-a-fernet-key")
    headers = auth_header(client, "sa1@example.com")
    setup = client.post("/api/v1/auth/sa-mfa/setup", headers=headers)
    assert setup.status_code == 503
