"""Super Admin TOTP recovery tests (Zenn's final decisions, 2026-10-26).

Covers: QR setup + confirm enrollment, password+TOTP login,
TOTP freeze (5 wrong -> 1h, no password lockout), TOTP recovery
login (lockout bypass, session revoke, via_recovery reset),
peer-confirmed RESET/DISABLE requests, direct disable of another
SA, and the enforced minimum of 2 active Super Admins.

The TOTP codes in these tests are computed by an INDEPENDENT
RFC 6238 implementation below — never by the app's own verifier —
so a bug in the app's verifier cannot hide behind the test.
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
from app.models.audit_log import AuditLog
from app.modules.auth import router as auth_router
from app.modules.users.models import Role, User

PASSWORD = "StrongPass!123"
NEW_PASSWORD = "BrandNewPass!456"


# ---------------------------------------------------------------------------
# Independent TOTP (RFC 6238) — test-only, never imports the app's verifier.
# ---------------------------------------------------------------------------


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

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sa-totp.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _init():
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c))
        async with factory() as db:
            sa_role = Role(id=uuid4(), name="Super Admin")
            admin_role = Role(id=uuid4(), name="Admin")
            emp_role = Role(id=uuid4(), name="Department User")
            db.add_all([sa_role, admin_role, emp_role])
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
            return {
                "sa_role": sa_role.id,
                "admin_role": admin_role.id,
                "emp_role": emp_role.id,
                "users": user_ids,
            }

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


def _recover_with_fresh_code(client, email, key):
    """Recovery after an earlier login/recovery already consumed the
    current code: retry until the next 30s window gives a fresh one.
    (Strict no-reuse: one code, one use — a test that needs a
    second use must cross a window boundary.)"""
    deadline = time.time() + 35
    while True:
        resp = client.post(
            "/api/v1/auth/sa-mfa/recover",
            json={"email": email, "code": totp_code(key)},
        )
        if resp.status_code == 200:
            return resp
        assert resp.status_code == 401
        _wait_for_fresh_window(deadline)


def _login_with_fresh_code(client, email, key, password=None):
    deadline = time.time() + 35
    while True:
        resp = login(client, email, password=password, totp=totp_code(key))
        if resp.status_code == 200:
            return resp
        assert resp.status_code == 401
        _wait_for_fresh_window(deadline)


def auth_header(client, email, password=PASSWORD, totp=None):
    resp = login(client, email, password, totp)
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def enroll(client, email):
    """Enroll an SA via setup+confirm; returns the manual key."""
    headers = auth_header(client, email)
    setup = client.post("/api/v1/auth/sa-mfa/setup", headers=headers)
    assert setup.status_code == 200, setup.text
    key = setup.json()["manual_key"]
    assert setup.json()["otpauth_url"].startswith("otpauth://totp/")
    confirm = client.post(
        "/api/v1/auth/sa-mfa/confirm", json={"code": totp_code(key)}, headers=headers
    )
    assert confirm.status_code == 200, confirm.text
    return key


def me(client, email, **kwargs):
    return client.get("/api/v1/auth/me", headers=auth_header(client, email, **kwargs))


def set_active(client, email, active):
    async def _run():
        async with client.factory() as db:
            user = (await db.execute(select(User).where(User.email == email))).scalars().first()
            user.is_active = active
            await db.commit()

    asyncio.run(_run())


def test_setup_and_confirm_enrolls(client):
    headers = auth_header(client, "sa1@example.com")
    setup = client.post("/api/v1/auth/sa-mfa/setup", headers=headers)
    assert setup.status_code == 200
    key = setup.json()["manual_key"]
    bad = client.post(
        "/api/v1/auth/sa-mfa/confirm", json={"code": "000000"}, headers=headers
    )
    assert bad.status_code == 400
    good = client.post(
        "/api/v1/auth/sa-mfa/confirm", json={"code": totp_code(key)}, headers=headers
    )
    assert good.status_code == 200
    status = client.get("/api/v1/auth/sa-mfa/status", headers=headers)
    assert status.json() == {
        "enrolled": True,
        "setup_required": False,
        "pending_request_kind": None,
    }
    assert me(client, "sa1@example.com", totp=totp_code(key)).json()["mfa_setup_required"] is False


def test_setup_again_after_enrolled_is_refused(client):
    key = enroll(client, "sa1@example.com")
    headers = auth_header(client, "sa1@example.com", totp=totp_code(key))
    assert client.post("/api/v1/auth/sa-mfa/setup", headers=headers).status_code == 400


def test_login_requires_totp_after_enrollment(client):
    key = enroll(client, "sa1@example.com")
    assert login(client, "sa1@example.com").json()["detail"]["code"] == "MFA_REQUIRED"
    assert login(client, "sa1@example.com", totp="000000").json()["detail"]["code"] == "MFA_INVALID"
    assert login(client, "sa1@example.com", totp=totp_code(key)).status_code == 200


def test_non_sa_login_unaffected(client):
    assert login(client, "admin@example.com").status_code == 200


def test_login_code_reuse_rejected_within_window(client):
    """Strict no-reuse (Zenn, 2026-10-26): a code consumed by a login
    is rejected when presented again inside its window."""
    key = enroll(client, "sa1@example.com")
    code = totp_code(key)
    assert login(client, "sa1@example.com", totp=code).status_code == 200
    resp = login(client, "sa1@example.com", totp=code)
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "MFA_INVALID"


def test_confirm_does_not_consume_step(client):
    """Confirm is verify-only: the SAME code value used for setup
    confirm still works for the immediate login afterwards. (This
    is the enroll -> immediate-login flow strict tracking must
    not break.)"""
    headers = auth_header(client, "sa1@example.com")
    setup = client.post("/api/v1/auth/sa-mfa/setup", headers=headers)
    assert setup.status_code == 200
    key = setup.json()["manual_key"]
    code = totp_code(key)
    assert client.post(
        "/api/v1/auth/sa-mfa/confirm", json={"code": code}, headers=headers
    ).status_code == 200
    assert login(client, "sa1@example.com", totp=code).status_code == 200


def test_enroll_then_immediate_login(client):
    """Enroll (setup + confirm) then log in right away with the
    current code — the natural first-login flow must keep working
    under strict no-reuse."""
    key = enroll(client, "sa1@example.com")
    assert login(client, "sa1@example.com", totp=totp_code(key)).status_code == 200


def test_recovery_code_consumed_for_login_and_recovery(client):
    """Recovery consumes (Zenn follow-up, 2026-10-26): a code used
    for recovery cannot be replayed for a login or another recovery
    inside its window (generic 401 for recovery, MFA_INVALID for
    login, neither feeding the freeze counter); after the window
    rolls over, a fresh code works (normal rotation intact)."""
    key = enroll(client, "sa1@example.com")
    code = totp_code(key)
    recovered = client.post(
        "/api/v1/auth/sa-mfa/recover",
        json={"email": "sa1@example.com", "code": code},
    )
    assert recovered.status_code == 200, recovered.text
    # Same code for a login -> rejected (consumed step).
    resp = login(client, "sa1@example.com", totp=code)
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "MFA_INVALID"
    # Same code for another recovery -> the same generic 401 as
    # every other recovery failure (no account information leaks).
    again = client.post(
        "/api/v1/auth/sa-mfa/recover",
        json={"email": "sa1@example.com", "code": code},
    )
    assert again.status_code == 401
    assert again.json()["detail"] == "Invalid email or authenticator code."
    # After the window rolls over, a fresh code recovers fine.
    deadline = time.time() + 35
    while True:
        fresh = client.post(
            "/api/v1/auth/sa-mfa/recover",
            json={"email": "sa1@example.com", "code": totp_code(key)},
        )
        if fresh.status_code == 200:
            break
        assert fresh.status_code == 401
        now = time.time()
        assert now < deadline, "fresh recovery never succeeded"
        time.sleep(min(30 - (now % 30) + 0.2, deadline - now))
    assert fresh.status_code == 200


def test_totp_freeze_after_five_wrong_codes(client):
    key = enroll(client, "sa1@example.com")
    for _ in range(5):
        assert login(client, "sa1@example.com", totp="000000").status_code == 401
    # Frozen: even the right code is refused...
    assert login(client, "sa1@example.com", totp=totp_code(key)).status_code == 401
    # ...but the password lockout was NOT triggered (MFA_REQUIRED,
    # not an account-locked error, proves the password still passes).
    assert login(client, "sa1@example.com").json()["detail"]["code"] == "MFA_REQUIRED"


def test_recover_locked_sa_bypasses_lockout_and_revokes_sessions(client):
    key = enroll(client, "sa1@example.com")
    old = login(client, "sa1@example.com", totp=totp_code(key))
    assert old.status_code == 200
    old_headers = {"Authorization": f"Bearer {old.json()['access_token']}"}
    assert client.get("/api/v1/auth/me", headers=old_headers).status_code == 200

    async def _lock():
        async with client.factory() as db:
            user = (await db.execute(select(User).where(User.email == "sa1@example.com"))).scalars().first()
            user.is_locked = True
            user.locked_until = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=29)
            await db.commit()

    asyncio.run(_lock())
    locked = login(client, "sa1@example.com", totp=totp_code(key))
    assert locked.status_code == 403
    assert locked.json()["detail"]["code"] == "ACCOUNT_LOCKED"

    # The earlier login consumed this window's code, so recovery
    # needs the next window's fresh code (strict no-reuse).
    recovered = _recover_with_fresh_code(client, "sa1@example.com", key)
    assert recovered.json()["must_change_password"] is True
    # Old session is dead; the recovery session works.
    assert client.get("/api/v1/auth/me", headers=old_headers).status_code == 401
    new_headers = {"Authorization": f"Bearer {recovered.json()['access_token']}"}
    assert client.get("/api/v1/auth/me", headers=new_headers).status_code == 200

    async def _audit():
        async with client.factory() as db:
            rows = (await db.execute(
                select(AuditLog).where(AuditLog.action == "SA_RECOVERY_LOGIN")
            )).scalars().all()
            return rows

    assert len(asyncio.run(_audit())) == 1


def test_recover_failures_are_generic(client):
    enroll(client, "sa1@example.com")
    for email, code in (
        ("nobody@example.com", "123456"),
        ("admin@example.com", "123456"),
        ("sa1@example.com", "000000"),
    ):
        resp = client.post("/api/v1/auth/sa-mfa/recover", json={"email": email, "code": code})
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Invalid email or authenticator code."
    # An SA without MFA enrolled is the same generic failure.
    resp = client.post(
        "/api/v1/auth/sa-mfa/recover", json={"email": "sa2@example.com", "code": "123456"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid email or authenticator code."


def test_reset_password_via_recovery_once_only(client):
    key = enroll(client, "sa1@example.com")
    recovered = client.post(
        "/api/v1/auth/sa-mfa/recover",
        json={"email": "sa1@example.com", "code": totp_code(key)},
    )
    assert recovered.status_code == 200
    headers = {"Authorization": f"Bearer {recovered.json()['access_token']}"}
    reset = client.post(
        "/api/v1/auth/sa-mfa/reset-password",
        json={"new_password": NEW_PASSWORD, "confirm_new_password": NEW_PASSWORD},
        headers=headers,
    )
    assert reset.status_code == 200, reset.text
    # The flag cleared: a second reset on the same session is refused.
    again = client.post(
        "/api/v1/auth/sa-mfa/reset-password",
        json={"new_password": "AnotherPass!789"},
        headers=headers,
    )
    assert again.status_code == 403
    # New password works (with a fresh TOTP code — recovery already
    # consumed this window's code).
    assert _login_with_fresh_code(
        client, "sa1@example.com", key, password=NEW_PASSWORD
    ).status_code == 200


def test_peer_reset_approve_clears_mfa(client):
    enroll(client, "sa1@example.com")
    sa2_key = enroll(client, "sa2@example.com")
    # Public request: generic reply even for an unknown email.
    assert client.post(
        "/api/v1/auth/sa-mfa/reset-requests", json={"email": "nobody@example.com"}
    ).status_code == 200
    req = client.post(
        "/api/v1/auth/sa-mfa/reset-requests", json={"email": "sa1@example.com"}
    )
    assert req.status_code == 200
    assert req.json()["message"].startswith("Request received")
    # Duplicate pending request is silently absorbed (same reply).
    assert client.post(
        "/api/v1/auth/sa-mfa/reset-requests", json={"email": "sa1@example.com"}
    ).status_code == 200

    sa2_headers = auth_header(client, "sa2@example.com", totp=totp_code(sa2_key))
    pending = client.get("/api/v1/auth/sa-mfa/requests", headers=sa2_headers)
    assert pending.status_code == 200
    assert len(pending.json()) == 1
    assert pending.json()[0]["kind"] == "RESET"
    req_id = pending.json()[0]["id"]
    approved = client.post(f"/api/v1/auth/sa-mfa/requests/{req_id}/approve", headers=sa2_headers)
    assert approved.status_code == 200, approved.text
    # sa1 signs in with password only now; setup flag is back on.
    assert login(client, "sa1@example.com").status_code == 200
    assert me(client, "sa1@example.com").json()["mfa_setup_required"] is True


def test_own_disable_request_needs_peer(client):
    sa1_key = enroll(client, "sa1@example.com")
    sa2_key = enroll(client, "sa2@example.com")
    sa1_headers = auth_header(client, "sa1@example.com", totp=totp_code(sa1_key))
    created = client.post("/api/v1/auth/sa-mfa/disable-requests", headers=sa1_headers)
    assert created.status_code == 200
    sa2_headers = auth_header(client, "sa2@example.com", totp=totp_code(sa2_key))
    pending = client.get("/api/v1/auth/sa-mfa/requests", headers=sa2_headers)
    assert pending.json()[0]["kind"] == "DISABLE"
    req_id = pending.json()[0]["id"]
    # Self-approve is refused...
    assert client.post(
        f"/api/v1/auth/sa-mfa/requests/{req_id}/approve", headers=sa1_headers
    ).status_code == 403
    # ...peer approval turns MFA off.
    assert client.post(
        f"/api/v1/auth/sa-mfa/requests/{req_id}/approve", headers=sa2_headers
    ).status_code == 200
    assert login(client, "sa1@example.com").status_code == 200


def test_reject_keeps_mfa(client):
    sa1_key = enroll(client, "sa1@example.com")
    sa2_key = enroll(client, "sa2@example.com")
    client.post("/api/v1/auth/sa-mfa/reset-requests", json={"email": "sa1@example.com"})
    sa2_headers = auth_header(client, "sa2@example.com", totp=totp_code(sa2_key))
    req_id = client.get("/api/v1/auth/sa-mfa/requests", headers=sa2_headers).json()[0]["id"]
    assert client.post(
        f"/api/v1/auth/sa-mfa/requests/{req_id}/reject", headers=sa2_headers
    ).status_code == 200
    assert login(client, "sa1@example.com").json()["detail"]["code"] == "MFA_REQUIRED"


def test_direct_disable_other_sa(client):
    enroll(client, "sa1@example.com")
    sa2_key = enroll(client, "sa2@example.com")
    sa2_headers = auth_header(client, "sa2@example.com", totp=totp_code(sa2_key))
    sa1_id = str(client.ids["users"]["sa1"])
    assert client.post(
        f"/api/v1/auth/sa-mfa/users/{sa1_id}/disable", headers=sa2_headers
    ).status_code == 200
    assert login(client, "sa1@example.com").status_code == 200
    # Own MFA cannot be disabled directly.
    sa2_id = str(client.ids["users"]["sa2"])
    assert client.post(
        f"/api/v1/auth/sa-mfa/users/{sa2_id}/disable", headers=sa2_headers
    ).status_code == 403


def test_disable_then_reenroll_audit_names_and_session_revoke(client):
    """MFA disable addendum (Zenn, 2026-10-26): peer-confirmed own
    disable -> password-only login, old sessions revoked, then QR
    setup again re-enables MFA. Audit: MFA_DISABLED (actor = the
    peer confirmer, confirmed_by explicit) + MFA_ENABLED on re-enable.
    Disabling MFA never touches the account itself (min-2 unaffected:
    both SAs stay active and re-enrollment works)."""
    sa1_key = enroll(client, "sa1@example.com")
    sa2_key = enroll(client, "sa2@example.com")
    sa1_headers = auth_header(client, "sa1@example.com", totp=totp_code(sa1_key))
    sa2_headers = auth_header(client, "sa2@example.com", totp=totp_code(sa2_key))
    assert client.post("/api/v1/auth/sa-mfa/disable-requests", headers=sa1_headers).status_code == 200
    req_id = client.get("/api/v1/auth/sa-mfa/requests", headers=sa2_headers).json()[0]["id"]
    assert client.post(
        f"/api/v1/auth/sa-mfa/requests/{req_id}/approve", headers=sa2_headers
    ).status_code == 200
    # Password-only login works again...
    assert login(client, "sa1@example.com").status_code == 200
    # ...but the pre-disable session is revoked.
    assert client.get("/api/v1/auth/me", headers=sa1_headers).status_code == 401
    # Re-enroll with a fresh QR setup: MFA is required again.
    new_key = enroll(client, "sa1@example.com")
    assert login(client, "sa1@example.com").json()["detail"]["code"] == "MFA_REQUIRED"
    # Strict no-reuse: if this login lands in the same 30s window
    # as sa1's earlier login, its step is already consumed — wait
    # for the next window and retry with the fresh step's code.
    deadline = time.time() + 35
    while True:
        resp = login(client, "sa1@example.com", totp=totp_code(new_key))
        if resp.status_code == 200:
            break
        assert resp.json()["detail"]["code"] == "MFA_INVALID"
        now = time.time()
        assert now < deadline, "re-enroll login never succeeded"
        time.sleep(min(30 - (now % 30) + 0.2, deadline - now))
    assert resp.status_code == 200
    # The other SA is untouched and still enrolled.
    assert login(client, "sa2@example.com").json()["detail"]["code"] == "MFA_REQUIRED"

    async def _audits():
        async with client.factory() as db:
            rows = (await db.execute(
                select(AuditLog).where(AuditLog.action.in_(["MFA_DISABLED", "MFA_ENABLED"]))
            )).scalars().all()
            return rows

    rows = asyncio.run(_audits())
    disabled = [r for r in rows if r.action == "MFA_DISABLED"]
    enabled = [r for r in rows if r.action == "MFA_ENABLED"]
    assert disabled, "no MFA_DISABLED audit row"
    assert disabled[-1].new_value["via"] == "peer-confirm"
    assert disabled[-1].new_value["confirmed_by"] == str(client.ids["users"]["sa2"])
    assert disabled[-1].record_id == str(client.ids["users"]["sa1"])
    assert enabled, "no MFA_ENABLED audit row"
    assert enabled[-1].record_id == str(client.ids["users"]["sa1"])


def test_min_two_enforced_on_demote_deactivate_delete(client):
    sa1_headers = auth_header(client, "sa1@example.com")
    sa2_headers = auth_header(client, "sa2@example.com")
    sa1_id = str(client.ids["users"]["sa1"])
    emp_role = str(client.ids["emp_role"])
    demote = client.put(
        f"/api/v1/users/{sa1_id}", json={"role_id": emp_role}, headers=sa1_headers
    )
    assert demote.status_code == 400, demote.text
    assert "at least 2 active Super Admins" in demote.json()["detail"]
    # Deactivate/delete are checked against ANOTHER SA (self-actions
    # are refused earlier by their own rule).
    assert client.patch(
        f"/api/v1/users/super-admins/{sa1_id}/deactivate", headers=sa2_headers
    ).status_code == 400
    assert client.delete(
        f"/api/v1/users/super-admins/{sa1_id}", headers=sa2_headers
    ).status_code == 400


def test_demote_allowed_with_three_sas(client):
    async def _add_sa3():
        async with client.factory() as db:
            user = User(
                id=uuid4(),
                email="sa3@example.com",
                first_name="sa3",
                last_name="Test",
                hashed_password=get_password_hash(PASSWORD),
                local_password_enabled=True,
                must_change_password=False,
                is_active=True,
                role_id=client.ids["sa_role"],
            )
            db.add(user)
            await db.commit()
            return user.id

    sa3_id = asyncio.run(_add_sa3())
    sa1_headers = auth_header(client, "sa1@example.com")
    demote = client.put(
        f"/api/v1/users/{sa3_id}", json={"role_id": str(client.ids["emp_role"])},
        headers=sa1_headers,
    )
    assert demote.status_code == 200, demote.text


def test_single_sa_warning_flag(client):
    # Two active SAs: no warning.
    assert me(client, "sa1@example.com").json()["single_sa_warning"] is False
    # Drop to one (direct DB change — the API itself blocks this).
    set_active(client, "sa2@example.com", False)
    assert me(client, "sa1@example.com").json()["single_sa_warning"] is True
    set_active(client, "sa2@example.com", True)
    assert me(client, "sa1@example.com").json()["single_sa_warning"] is False


def test_setup_without_key_fails_closed(client, monkeypatch):
    monkeypatch.setattr(settings, "TOTP_ENCRYPTION_KEY", None)
    headers = auth_header(client, "sa1@example.com")
    resp = client.post("/api/v1/auth/sa-mfa/setup", headers=headers)
    assert resp.status_code == 503
