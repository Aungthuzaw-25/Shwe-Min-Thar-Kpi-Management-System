"""MFA confirm/disable freeze (2026-11-13, hardening).

The login-freeze pattern (5 wrong TOTP codes -> 1-hour freeze,
TOTP entry only) now also guards the three code-proving endpoints
that used to call ``verify_code`` directly with no counter:

- ``POST /user-mfa/disable`` (sharpest: a stolen session could
  otherwise guess across 30s windows without a freeze)
- ``POST /user-mfa/confirm``
- ``POST /sa-mfa/confirm``

Frozen and wrong codes fail with the same generic 400 as before
(no oracle, no new error shape — exactly like login's generic
MFA_INVALID). The password is never locked by a TOTP freeze, and
peer-SA reset + break-glass still work during a freeze (both clear
the attempt row via ``clear_totp_for_user``).

The TOTP codes here are computed by an INDEPENDENT RFC 6238
implementation — never by the app's own verifier.
"""

import asyncio
import base64
import hashlib
import hmac
import struct
import time
from datetime import datetime, timedelta, timezone
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
from app.modules.auth.models import SATotpAttempt
from app.modules.users.models import Role, User

PASSWORD = "StrongPass!123"
BREAK_GLASS_TOKEN = "break-glass-token-for-tests-0123456789"


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
    monkeypatch.setattr(settings, "TOTP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "SA_MFA_BREAK_GLASS_TOKEN", BREAK_GLASS_TOKEN)
    monkeypatch.setattr(cache_module, "_client", FakeRedis())
    monkeypatch.setattr(cache_module, "_client_unavailable", False)
    auth_router.limiter.enabled = False
    if hasattr(app.state, "limiter"):
        app.state.limiter.enabled = False

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mfa-freeze.db'}")
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
                ("hr", "HR"),
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


def _login_with_fresh_code(client, email, key, password=None):
    deadline = time.time() + 35
    while True:
        resp = login(client, email, password=password or PASSWORD, totp=totp_code(key))
        if resp.status_code == 200:
            return resp
        assert resp.status_code == 401, resp.text
        _wait_for_fresh_window(deadline)


def auth_header(client, email, password=PASSWORD, totp=None):
    resp = login(client, email, password, totp)
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def enroll_user(client, email):
    """Enroll an Admin/HR user via /user-mfa/*; returns the manual key."""
    headers = auth_header(client, email)
    setup = client.post("/api/v1/auth/user-mfa/setup", headers=headers)
    assert setup.status_code == 200, setup.text
    key = setup.json()["manual_key"]
    confirm = client.post(
        "/api/v1/auth/user-mfa/confirm", json={"code": totp_code(key)}, headers=headers
    )
    assert confirm.status_code == 200, confirm.text
    return key


def enroll_sa(client, email):
    headers = auth_header(client, email)
    setup = client.post("/api/v1/auth/sa-mfa/setup", headers=headers)
    assert setup.status_code == 200, setup.text
    key = setup.json()["manual_key"]
    confirm = client.post(
        "/api/v1/auth/sa-mfa/confirm", json={"code": totp_code(key)}, headers=headers
    )
    assert confirm.status_code == 200, confirm.text
    return key


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


def _user_row(client, email):
    async def _run():
        async with client.factory() as db:
            return (
                await db.execute(select(User).where(User.email == email))
            ).scalars().first()

    return asyncio.run(_run())


def _expire_freeze(client, email):
    """Move the freeze into the past (the 1-hour expiry), like time passing."""
    async def _run():
        async with client.factory() as db:
            user = (
                await db.execute(select(User).where(User.email == email))
            ).scalars().first()
            row = (
                await db.execute(
                    select(SATotpAttempt).where(SATotpAttempt.user_id == user.id)
                )
            ).scalars().first()
            assert row is not None and row.frozen_until is not None
            row.frozen_until = datetime.now(timezone.utc).replace(
                tzinfo=None
            ) - timedelta(hours=2)
            await db.commit()

    asyncio.run(_run())


def _freeze_via_disable(client, email, key):
    """Drive /user-mfa/disable with 5 wrong codes; returns the auth headers."""
    headers = auth_header(client, email, totp=totp_code(key))
    for _ in range(5):
        resp = client.post(
            "/api/v1/auth/user-mfa/disable",
            json={"code": _wrong_code(key)},
            headers=headers,
        )
        assert resp.status_code == 400, resp.text
    return headers


def test_disable_freezes_after_five_wrong_codes(client):
    key = enroll_user(client, "admin@example.com")
    headers = auth_header(client, "admin@example.com", totp=totp_code(key))

    # 1-4 wrong: still allowed — the counter climbs, no freeze yet.
    for n in range(1, 5):
        resp = client.post(
            "/api/v1/auth/user-mfa/disable",
            json={"code": _wrong_code(key)},
            headers=headers,
        )
        assert resp.status_code == 400, resp.text
        row = _attempt_row(client, "admin@example.com")
        assert row.failed_attempts == n
        assert row.frozen_until is None

    # 5th wrong: frozen now (same generic 400 — no oracle).
    resp = client.post(
        "/api/v1/auth/user-mfa/disable",
        json={"code": _wrong_code(key)},
        headers=headers,
    )
    assert resp.status_code == 400, resp.text
    row = _attempt_row(client, "admin@example.com")
    assert row.failed_attempts == 5
    assert row.frozen_until is not None

    # Frozen: even the correct code is refused, on disable AND on
    # login (the counter is shared per account) — and frozen
    # attempts do not feed the counter further.
    assert client.post(
        "/api/v1/auth/user-mfa/disable",
        json={"code": totp_code(key)},
        headers=headers,
    ).status_code == 400
    resp = login(client, "admin@example.com", totp=totp_code(key))
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "MFA_INVALID"
    row = _attempt_row(client, "admin@example.com")
    assert row.failed_attempts == 5
    assert row.frozen_until is not None


def test_disable_freeze_expires_after_one_hour(client):
    key = enroll_user(client, "admin@example.com")
    headers = _freeze_via_disable(client, "admin@example.com", key)

    _expire_freeze(client, "admin@example.com")
    # A fresh start, not a permanent lock: the correct code works.
    ok = client.post(
        "/api/v1/auth/user-mfa/disable",
        json={"code": totp_code(key)},
        headers=headers,
    )
    assert ok.status_code == 200, ok.text
    assert _attempt_row(client, "admin@example.com") is None
    assert login(client, "admin@example.com").status_code == 200


def test_confirm_freezes_after_five_wrong_codes(client):
    headers = auth_header(client, "admin@example.com")
    setup = client.post("/api/v1/auth/user-mfa/setup", headers=headers)
    assert setup.status_code == 200, setup.text
    key = setup.json()["manual_key"]

    for n in range(1, 5):
        resp = client.post(
            "/api/v1/auth/user-mfa/confirm",
            json={"code": _wrong_code(key)},
            headers=headers,
        )
        assert resp.status_code == 400, resp.text
        row = _attempt_row(client, "admin@example.com")
        assert row.failed_attempts == n
        assert row.frozen_until is None

    resp = client.post(
        "/api/v1/auth/user-mfa/confirm",
        json={"code": _wrong_code(key)},
        headers=headers,
    )
    assert resp.status_code == 400, resp.text
    row = _attempt_row(client, "admin@example.com")
    assert row.failed_attempts == 5
    assert row.frozen_until is not None

    # Frozen: the correct code is refused.
    assert client.post(
        "/api/v1/auth/user-mfa/confirm",
        json={"code": totp_code(key)},
        headers=headers,
    ).status_code == 400

    # After the hour passes, the same code enrolls the user.
    _expire_freeze(client, "admin@example.com")
    ok = client.post(
        "/api/v1/auth/user-mfa/confirm",
        json={"code": totp_code(key)},
        headers=headers,
    )
    assert ok.status_code == 200, ok.text


def test_password_never_locked_by_totp_freeze(client):
    key = enroll_user(client, "admin@example.com")
    _freeze_via_disable(client, "admin@example.com", key)

    # The freeze is TOTP entry only: no password lockout, no
    # failed-login count — and another account is unaffected.
    user = _user_row(client, "admin@example.com")
    assert user.is_locked is False
    assert user.failed_login_attempts == 0
    assert login(client, "hr@example.com").status_code == 200

    # After the freeze expires, password login with the code works.
    _expire_freeze(client, "admin@example.com")
    assert _login_with_fresh_code(
        client, "admin@example.com", key
    ).status_code == 200


def test_peer_sa_reset_recovers_frozen_admin(client):
    key = enroll_user(client, "admin@example.com")
    _freeze_via_disable(client, "admin@example.com", key)

    # Recovery: the admin asks, a peer SA approves (never self).
    req = client.post(
        "/api/v1/auth/user-mfa/reset-requests",
        json={"email": "admin@example.com"},
    )
    assert req.status_code == 200
    sa_headers = auth_header(client, "sa1@example.com")
    listing = client.get("/api/v1/auth/sa-mfa/requests", headers=sa_headers)
    assert listing.status_code == 200
    pending = [r for r in listing.json() if r["email"] == "admin@example.com"]
    assert len(pending) == 1
    approve = client.post(
        f"/api/v1/auth/sa-mfa/requests/{pending[0]['id']}/approve",
        headers=sa_headers,
    )
    assert approve.status_code == 200, approve.text

    # MFA is off and the freeze row is gone: password-only login
    # works, and the admin can enroll again from scratch.
    assert login(client, "admin@example.com").status_code == 200
    assert _attempt_row(client, "admin@example.com") is None
    enroll_user(client, "admin@example.com")


def test_sa_confirm_freezes_after_five_wrong_codes(client):
    headers = auth_header(client, "sa1@example.com")
    setup = client.post("/api/v1/auth/sa-mfa/setup", headers=headers)
    assert setup.status_code == 200, setup.text
    key = setup.json()["manual_key"]

    for n in range(1, 5):
        resp = client.post(
            "/api/v1/auth/sa-mfa/confirm",
            json={"code": _wrong_code(key)},
            headers=headers,
        )
        assert resp.status_code == 400, resp.text
        row = _attempt_row(client, "sa1@example.com")
        assert row.failed_attempts == n
        assert row.frozen_until is None

    resp = client.post(
        "/api/v1/auth/sa-mfa/confirm",
        json={"code": _wrong_code(key)},
        headers=headers,
    )
    assert resp.status_code == 400, resp.text
    row = _attempt_row(client, "sa1@example.com")
    assert row.failed_attempts == 5
    assert row.frozen_until is not None

    # Frozen: the correct code is refused — but the SA is not
    # enrolled yet, so password-only login still works (the freeze
    # blocks TOTP entry only, not the account).
    assert client.post(
        "/api/v1/auth/sa-mfa/confirm",
        json={"code": totp_code(key)},
        headers=headers,
    ).status_code == 400
    assert login(client, "sa1@example.com").status_code == 200

    _expire_freeze(client, "sa1@example.com")
    # Fresh session: the password-only login above revoked the
    # original one (per-device sessions).
    headers = auth_header(client, "sa1@example.com")
    ok = client.post(
        "/api/v1/auth/sa-mfa/confirm",
        json={"code": totp_code(key)},
        headers=headers,
    )
    assert ok.status_code == 200, ok.text


def test_break_glass_recovers_frozen_sa(client):
    # Break-glass needs a confirmed secret, so the freeze here comes
    # from login (an enrolled SA cannot reach /sa-mfa/confirm at
    # all). The point: recovery ignores the freeze entirely.
    key = enroll_sa(client, "sa1@example.com")
    for _ in range(5):
        assert login(client, "sa1@example.com", totp=_wrong_code(key)).status_code == 401
    resp = login(client, "sa1@example.com", totp=totp_code(key))
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "MFA_INVALID"

    resp = client.post(
        "/api/v1/auth/sa-mfa/break-glass",
        json={
            "email": "sa1@example.com",
            "password": PASSWORD,
            "break_glass_token": BREAK_GLASS_TOKEN,
        },
    )
    assert resp.status_code == 200, resp.text

    # MFA is off and the freeze row is gone: password-only login
    # works, and the SA can enroll again from scratch.
    assert login(client, "sa1@example.com").status_code == 200
    assert _attempt_row(client, "sa1@example.com") is None
    enroll_sa(client, "sa1@example.com")
