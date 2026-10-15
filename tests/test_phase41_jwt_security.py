from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import jwt
import pytest
from fastapi import HTTPException, Response
from jwt import PyJWTError as JWTError
from starlette.requests import Request

from app.core import dependencies, security
from app.core.config import Settings, settings
from app.modules.auth import router as auth_router
from app.modules.auth.models import TokenBlacklist
from app.modules.auth.schemas import ChangePasswordRequest, RefreshTokenRequest


class _Scalars:
    def __init__(self, value):
        self.value = value

    def first(self):
        return self.value

    def all(self):
        return self.value if isinstance(self.value, list) else ([] if self.value is None else [self.value])


class _Result:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return _Scalars(self.value)


class _RefreshDB:
    def __init__(self, user, session):
        self.user = user
        self.session = session
        self.blacklisted = set()
        self.commits = 0
        self.mode = "refresh"
        self.stage = 0

    async def execute(self, _statement):
        self.stage += 1
        if self.mode == "refresh":
            if self.stage == 1:
                return _Result(SimpleNamespace() if self.blacklisted else None)
            if self.stage == 2:
                return _Result(self.user)
            return _Result(self.session if self.session.revoked_at is None else None)
        return _Result([])

    def add(self, value):
        if isinstance(value, TokenBlacklist):
            self.blacklisted.add(value.token_hash)

    async def commit(self):
        self.commits += 1

    async def get(self, _model, _session_id):
        return self.session


def _request(access_token=None, refresh_token=None):
    headers = []
    if access_token:
        headers.append((b"authorization", f"Bearer {access_token}".encode()))
    if refresh_token:
        headers.append((b"cookie", f"refresh_token={refresh_token}".encode()))
    return Request({
        "type": "http", "method": "POST", "path": "/auth/refresh",
        "headers": headers, "query_string": b"", "scheme": "http",
        "server": ("testserver", 80), "client": ("testclient", 1234),
    })


def _session_fixture():
    user_id, session_id = uuid4(), uuid4()
    user = SimpleNamespace(
        id=user_id, email="alice@example.com", is_deleted=False, is_active=True,
        role=SimpleNamespace(name="Admin"), role_id=uuid4(),
        user_departments=[SimpleNamespace(user_level="L1")],
    )
    session = SimpleNamespace(
        id=session_id, user_id=user_id, remember_me=True, revoked_at=None,
        refresh_token_hash="", last_used_at=None,
    )
    token_data = {"sub": user.email, "user_id": str(user_id), "sid": str(session_id)}
    return user, session, token_data


def _signed_payload(payload, key=None):
    return jwt.encode(payload, key or settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def test_tokens_include_server_controlled_claims_and_validate_by_type():
    token_data = {"sub": "alice@example.com", "user_id": str(uuid4()), "sid": str(uuid4())}
    access = security.create_access_token(token_data)
    refresh = security.create_refresh_token(token_data)

    access_claims = security.decode_token(access, "access", require_session=True)
    refresh_claims = security.decode_token(refresh, "refresh", require_session=True)
    for claims, token_type in ((access_claims, "access"), (refresh_claims, "refresh")):
        assert claims["sub"] == "alice@example.com"
        assert claims["type"] == token_type
        assert claims["exp"] >= claims["iat"]
        assert claims["jti"]
        assert claims["sid"] == token_data["sid"]

    with pytest.raises(JWTError):
        security.decode_token(refresh, "access", require_session=True)
    with pytest.raises(JWTError):
        security.decode_token(access, "refresh", require_session=True)


@pytest.mark.parametrize("payload", [
    {"sub": "alice@example.com", "type": "access", "iat": 1, "jti": "j", "exp": 1},
    {"type": "access", "iat": 1, "jti": "j", "exp": datetime.now(UTC) + timedelta(minutes=5)},
])
def test_expired_or_missing_subject_access_tokens_are_rejected(payload):
    token = _signed_payload(payload)
    with pytest.raises(JWTError):
        security.decode_token(token, "access")


def test_invalid_signature_and_missing_session_claims_are_rejected():
    token_data = {"sub": "alice@example.com", "user_id": str(uuid4()), "sid": str(uuid4())}
    valid = security.create_access_token(token_data)
    claims = security.decode_token(valid, "access")
    invalid_signature_token = _signed_payload(claims, key="wrong-secret")
    with pytest.raises(JWTError):
        security.decode_token(invalid_signature_token, "access")

    missing_session = security.create_access_token({"sub": "alice@example.com"})
    with pytest.raises(JWTError):
        security.decode_token(missing_session, "access", require_session=True)


@pytest.mark.asyncio
async def test_access_endpoint_rejects_refresh_token_and_refresh_endpoint_rejects_access_token():
    user, session, token_data = _session_fixture()
    refresh = security.create_refresh_token(token_data)
    access = security.create_access_token(token_data)
    request = _request()
    db = SimpleNamespace()

    with pytest.raises(HTTPException) as access_error:
        await dependencies.get_current_user(request, refresh, db)
    assert access_error.value.status_code == 401

    db = _RefreshDB(user, session)
    response = Response()
    refresh_request = RefreshTokenRequest(refresh_token=access)
    with pytest.raises(HTTPException) as refresh_error:
        await auth_router.refresh_token(request, response, refresh_request, db)
    assert refresh_error.value.status_code == 401


@pytest.mark.asyncio
async def test_refresh_rotation_replay_and_logout_revocation():
    user, session, token_data = _session_fixture()
    refresh = security.create_refresh_token(token_data)
    session.refresh_token_hash = security.token_digest(refresh)
    db = _RefreshDB(user, session)

    rotated = await auth_router.refresh_token(
        _request(), Response(), RefreshTokenRequest(refresh_token=refresh), db
    )
    assert rotated["refresh_token"] != refresh
    assert security.token_digest(refresh) in db.blacklisted
    assert session.refresh_token_hash == security.token_digest(rotated["refresh_token"])
    security.decode_token(rotated["access_token"], "access", require_session=True)

    db.stage = 0
    request = _request()
    response = Response()
    replay_request = RefreshTokenRequest(refresh_token=refresh)
    with pytest.raises(HTTPException) as replay:
        await auth_router.refresh_token(request, response, replay_request, db)
    assert replay.value.status_code == 401

    db.mode = "logout"
    db.stage = 0
    await auth_router.logout(
        _request(), Response(), RefreshTokenRequest(refresh_token=rotated["refresh_token"]), db
    )
    assert session.revoked_at is not None

    db.mode = "refresh"
    db.stage = 0
    revoked_request = RefreshTokenRequest(refresh_token=rotated["refresh_token"])
    with pytest.raises(HTTPException) as revoked:
        await auth_router.refresh_token(request, response, revoked_request, db)
    assert revoked.value.status_code == 401


@pytest.mark.asyncio
async def test_password_change_revokes_existing_sessions(monkeypatch):
    user_id = uuid4()
    current_session_id = uuid4()
    user = SimpleNamespace(
        id=user_id, hashed_password="old-hash", local_password_enabled=True,  # noqa: S106 - synthetic test fixture; not a production credential
        must_change_password=True,
    )
    statements = []
    audit_entries = []

    class _UpdateResult:
        rowcount = 2

    async def execute(statement):
        statements.append(statement)
        return _UpdateResult()

    async def commit():
        db.commits += 1

    async def threadpool(func, *args):
        return func(*args)

    async def policy(_db):
        return {}

    async def history(_db, _user_id, _password):
        return True

    async def add_history(*_args, **_kwargs):
        return None

    db = SimpleNamespace(commits=0, execute=execute, add=audit_entries.append)
    db.commit = commit
    monkeypatch.setattr(auth_router, "run_in_threadpool", threadpool)
    monkeypatch.setattr(auth_router.security, "verify_password", lambda *_: True)
    monkeypatch.setattr(auth_router.security, "get_password_hash", lambda *_: "new-hash")
    monkeypatch.setattr(auth_router, "get_password_policy", policy)
    monkeypatch.setattr(auth_router.password_repo, "check_password_history", history)
    monkeypatch.setattr(auth_router.password_repo, "add_password_history", add_history)
    monkeypatch.setattr(
        auth_router.security,
        "decode_token",
        lambda *_args, **_kwargs: {"sid": str(current_session_id), "user_id": str(user_id)},
    )

    await auth_router.self_change_password(
        _request(), ChangePasswordRequest(old_password="old", new_password="StrongPass!123"), user, db  # noqa: S106 - synthetic test fixture; not a production credential
    )
    assert user.hashed_password == "new-hash"  # noqa: S105 - synthetic test fixture; not a production credential
    assert current_session_id in statements[0].compile().params.values()
    assert audit_entries[0].action == "PASSWORD_CHANGED"
    assert audit_entries[0].new_value == {"other_sessions_revoked": 2}
    assert db.commits == 1


def test_production_configuration_rejects_placeholder_secrets_and_non_hs256():
    configured = Settings.model_construct(
        ENV="production", SECRET_KEY="CHANGE_ME_GENERATE_A_LONG_RANDOM_VALUE",  # noqa: S106 - synthetic test fixture; not a production credential
        ALLOWED_ORIGINS="https://app.example.com", USE_S3=True,
        S3_ENDPOINT="https://storage.example.com", S3_ACCESS_KEY="access", S3_SECRET_KEY="secret",  # noqa: S106 - synthetic test fixture; not a production credential
    )
    with pytest.raises(ValueError, match="SECRET_KEY"):
        configured.validate_security_settings()
    with pytest.raises(ValueError, match="ALGORITHM"):
        Settings.validate_jwt_algorithm("HS384")