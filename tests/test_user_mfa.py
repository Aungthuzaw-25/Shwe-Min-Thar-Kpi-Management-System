"""Optional TOTP MFA for Admin/HR users (2026-11-06).

Covers: opt-in enrollment (setup/confirm) + login with the code;
unenrolled login unchanged; wrong codes freeze TOTP only (the
password is never locked by a TOTP freeze); self-disable requires
the current code; peer-SA reset recovers a locked-out Admin/HR
user; SA-only paths refuse non-SA callers; SA login is unchanged.

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
from app.modules.auth.models import SAMfaRequest
from app.modules.users.models import Role, User

PASSWORD = "StrongPass!123"


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
    monkeypatch.setattr(settings, "TOTP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(cache_module, "_client", FakeRedis())
    monkeypatch.setattr(cache_module, "_client_unavailable", False)
    auth_router.limiter.enabled = False
    if hasattr(app.state, "limiter"):
        app.state.limiter.enabled = False

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'user-mfa.db'}")
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
    assert setup.json()["otpauth_url"].startswith("otpauth://totp/")
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


def user_mfa_status(client, email, key=None):
    """Read /user-mfa/status. An enrolled user needs a code to log in,
    so pass their manual key (a fresh window is awaited if the
    current code was already consumed)."""
    if key is not None:
        resp = _login_with_fresh_code(client, email, key)
    else:
        resp = login(client, email)
    assert resp.status_code == 200, resp.text
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    resp = client.get("/api/v1/auth/user-mfa/status", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _user_row(client, email):
    async def _run():
        async with client.factory() as db:
            return (await db.execute(
                select(User).where(User.email == email)
            )).scalars().first()

    return asyncio.run(_run())


def test_admin_enroll_then_login_with_code(client):
    # Unenrolled: login is exactly as before — no code, no prompt.
    assert login(client, "admin@example.com").status_code == 200

    key = enroll_user(client, "admin@example.com")
    assert user_mfa_status(client, "admin@example.com", key)["enrolled"] is True

    # No code -> MFA_REQUIRED; wrong code -> MFA_INVALID.
    resp = login(client, "admin@example.com")
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "MFA_REQUIRED"
    resp = login(client, "admin@example.com", totp="000000")
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "MFA_INVALID"

    # Right code -> in, on every device (no trusted-device bypass
    # is a property of the login flow, not of the test).
    assert _login_with_fresh_code(client, "admin@example.com", key).status_code == 200


def test_hr_enroll_and_login(client):
    key = enroll_user(client, "hr@example.com")
    assert user_mfa_status(client, "hr@example.com", key)["enrolled"] is True
    assert _login_with_fresh_code(client, "hr@example.com", key).status_code == 200


def test_unenrolled_hr_login_unchanged(client):
    assert login(client, "hr@example.com").status_code == 200
    assert login(client, "hr@example.com", totp="123456").status_code == 200


def test_wrong_codes_freeze_totp_only_and_peer_reset_recovers(client):
    key = enroll_user(client, "admin@example.com")
    for _ in range(5):
        assert login(client, "admin@example.com", totp="000000").status_code == 401

    # Frozen: even the right code fails — but the PASSWORD is not
    # locked (no password lockout, no failed-login count).
    resp = login(client, "admin@example.com", totp=totp_code(key))
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "MFA_INVALID"
    user = _user_row(client, "admin@example.com")
    assert user.is_locked is False
    assert user.failed_login_attempts == 0

    # Recovery: peer-SA reset (the SA approves, never self-approved).
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
    assert login(client, "admin@example.com").status_code == 200


def test_disable_requires_current_code(client):
    key = enroll_user(client, "admin@example.com")
    headers = auth_header(client, "admin@example.com", totp=totp_code(key))

    # Wrong code -> 400 and MFA stays on: the right-code disable
    # below succeeding is itself the proof (it 400s when MFA is
    # not enrolled). No fresh login in between — a new login from
    # the same device revokes this session (per-device sessions).
    wrong = client.post(
        "/api/v1/auth/user-mfa/disable", json={"code": "000000"}, headers=headers
    )
    assert wrong.status_code == 400

    # Disable verifies the code by value only (no step consumption),
    # so the code the login just consumed still proves possession.
    ok = client.post(
        "/api/v1/auth/user-mfa/disable",
        json={"code": totp_code(key)},
        headers=headers,
    )
    assert ok.status_code == 200, ok.text
    assert login(client, "admin@example.com").status_code == 200
    assert user_mfa_status(client, "admin@example.com")["enrolled"] is False


def test_sa_paths_refuse_non_sa_and_user_mfa_refuses_sa_and_member(client):
    key = enroll_user(client, "admin@example.com")

    # An enrolled Admin cannot use the SA recovery path.
    recover = client.post(
        "/api/v1/auth/sa-mfa/recover",
        json={"email": "admin@example.com", "code": totp_code(key)},
    )
    assert recover.status_code == 401
    admin_headers = auth_header(client, "admin@example.com", totp=totp_code(key))
    assert client.post("/api/v1/auth/sa-mfa/setup", headers=admin_headers).status_code == 403

    # An SA reset request for an Admin creates nothing (generic
    # reply, no row) — the Admin path is /user-mfa/reset-requests.
    resp = client.post(
        "/api/v1/auth/sa-mfa/reset-requests",
        json={"email": "admin@example.com"},
    )
    assert resp.status_code == 200

    async def _requests():
        async with client.factory() as db:
            return (await db.execute(select(SAMfaRequest))).scalars().all()

    assert asyncio.run(_requests()) == []

    # An SA cannot enroll via the Admin/HR endpoints, and neither
    # can a Department User.
    sa_headers = auth_header(client, "sa1@example.com")
    assert client.post("/api/v1/auth/user-mfa/setup", headers=sa_headers).status_code == 403
    member_headers = auth_header(client, "member@example.com")
    assert client.post("/api/v1/auth/user-mfa/setup", headers=member_headers).status_code == 403


def test_peer_sa_reset_recovers_admin_and_audits(client):
    enroll_user(client, "admin@example.com")
    resp = client.post(
        "/api/v1/auth/user-mfa/reset-requests",
        json={"email": "admin@example.com"},
    )
    assert resp.status_code == 200
    sa_headers = auth_header(client, "sa1@example.com")
    listing = client.get("/api/v1/auth/sa-mfa/requests", headers=sa_headers)
    pending = [r for r in listing.json() if r["email"] == "admin@example.com"]
    assert len(pending) == 1
    approve = client.post(
        f"/api/v1/auth/sa-mfa/requests/{pending[0]['id']}/approve",
        headers=sa_headers,
    )
    assert approve.status_code == 200, approve.text
    assert login(client, "admin@example.com").status_code == 200

    async def _audit():
        async with client.factory() as db:
            return (await db.execute(
                select(AuditLog).where(AuditLog.action == "MFA_RESET_CONFIRMED")
            )).scalars().all()

    rows = asyncio.run(_audit())
    assert len(rows) == 1
    assert rows[0].new_value.get("via") == "peer-confirm"


def test_admin_cannot_approve_requests(client):
    key = enroll_user(client, "admin@example.com")
    client.post(
        "/api/v1/auth/user-mfa/reset-requests",
        json={"email": "admin@example.com"},
    )
    sa_headers = auth_header(client, "sa1@example.com")
    listing = client.get("/api/v1/auth/sa-mfa/requests", headers=sa_headers)
    pending = [r for r in listing.json() if r["email"] == "admin@example.com"]
    assert len(pending) == 1
    admin_headers = auth_header(client, "admin@example.com", totp=totp_code(key))
    resp = client.post(
        f"/api/v1/auth/sa-mfa/requests/{pending[0]['id']}/approve",
        headers=admin_headers,
    )
    assert resp.status_code == 403


def test_sa_direct_disable_admin(client):
    enroll_user(client, "admin@example.com")
    sa_headers = auth_header(client, "sa1@example.com")
    admin_id = str(client.ids["users"]["admin"])
    resp = client.post(
        f"/api/v1/auth/sa-mfa/users/{admin_id}/disable", headers=sa_headers
    )
    assert resp.status_code == 200, resp.text
    assert login(client, "admin@example.com").status_code == 200


def test_sa_login_unchanged(client):
    key = enroll_sa(client, "sa1@example.com")
    assert login(client, "sa1@example.com").status_code == 401
    assert _login_with_fresh_code(client, "sa1@example.com", key).status_code == 200
