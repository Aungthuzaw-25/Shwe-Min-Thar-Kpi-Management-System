from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from passlib.hash import bcrypt
from starlette.requests import Request

from app.core.password_policy import MAX_PASSWORD_LENGTH, validate_password_strength
from app.core.security import get_password_hash, verify_password
from app.modules.auth import router as auth_router
from app.modules.auth.schemas import ChangePasswordRequest, TokenResponse
from app.modules.users import router as users_router
from app.modules.users.password_schemas import PasswordResetAdmin
from app.modules.users.schemas import UserResponse


class _Scalars:
    def __init__(self, value):
        self.value = value

    def first(self):
        return self.value


class _Result:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return _Scalars(self.value)


def _request():
    return Request({
        "type": "http", "method": "POST", "path": "/auth/change-password",
        "headers": [], "query_string": b"", "scheme": "http",
        "server": ("testserver", 80), "client": ("testclient", 1234),
    })


def test_password_policy_enforces_minimum_maximum_weak_and_whitespace():
    assert validate_password_strength("StrongPass!12") is True
    with pytest.raises(HTTPException, match="at least"):
        validate_password_strength("Short!1")
    with pytest.raises(HTTPException, match="commonly used"):
        validate_password_strength("Password123", require_complexity=False)
    with pytest.raises(HTTPException, match="whitespace"):
        validate_password_strength(" " * 12)

    maximum = "x" * MAX_PASSWORD_LENGTH
    assert validate_password_strength(maximum, minimum_length=1, require_complexity=False) is True
    with pytest.raises(HTTPException, match="no more than"):
        validate_password_strength("x" * (MAX_PASSWORD_LENGTH + 1), minimum_length=1, require_complexity=False)


def test_unicode_passwords_are_validated_by_character_policy_without_truncation():
    password = "မြန်မာStrong!123"  # noqa: S105 - synthetic test fixture; not a production credential
    validate_password_strength(password)
    hashed = get_password_hash(password)
    assert hashed.startswith("$argon2id$")
    assert verify_password(password, hashed) is True
    assert verify_password(password + "x", hashed) is False

    long_unicode = "က" * MAX_PASSWORD_LENGTH
    assert validate_password_strength(long_unicode, minimum_length=1, require_complexity=False) is True
    with pytest.raises(HTTPException, match="no more than"):
        validate_password_strength(long_unicode + "က", minimum_length=1, require_complexity=False)


def test_new_hashes_use_argon2id_and_reject_oversized_input():
    password = "StrongPass!123"  # noqa: S105 - synthetic test fixture; not a production credential
    hashed = get_password_hash(password)
    assert hashed.startswith("$argon2id$")
    assert verify_password(password, hashed) is True
    assert verify_password("wrong-password", hashed) is False
    with pytest.raises(ValueError, match="no more than"):
        get_password_hash("x" * (MAX_PASSWORD_LENGTH + 1))


def test_legacy_bcrypt_verifies_at_72_bytes_but_never_truncates_73_bytes():
    legacy_password = "A" * 72
    legacy_hash = bcrypt.using(rounds=12).hash(legacy_password)

    assert verify_password(legacy_password, legacy_hash) is True
    assert verify_password(legacy_password + "B", legacy_hash) is False


@pytest.mark.asyncio
async def test_password_change_rejects_bad_current_password_and_confirmation(monkeypatch):
    user = SimpleNamespace(
        id=uuid4(), hashed_password="old-hash", local_password_enabled=True,  # noqa: S106 - synthetic test fixture; not a production credential
        must_change_password=False,
    )
    db = SimpleNamespace()

    async def threadpool(func, *args):
        return func(*args)

    monkeypatch.setattr(auth_router, "run_in_threadpool", threadpool)
    monkeypatch.setattr(auth_router.security, "verify_password", lambda *_: False)
    with pytest.raises(HTTPException, match="current password"):
        await auth_router.self_change_password(
            _request(), ChangePasswordRequest(
                old_password="wrong", new_password="StrongPass!123",  # noqa: S106 - synthetic test fixture; not a production credential
                confirm_new_password="StrongPass!123",  # noqa: S106 - synthetic test fixture; not a production credential
            ), user, db,
        )

    monkeypatch.setattr(auth_router.security, "verify_password", lambda *_: True)
    with pytest.raises(HTTPException, match="confirmation"):
        await auth_router.self_change_password(
            _request(), ChangePasswordRequest(
                old_password="old", new_password="StrongPass!123",  # noqa: S106 - synthetic test fixture; not a production credential
                confirm_new_password="DifferentPass!123",  # noqa: S106 - synthetic test fixture; not a production credential
            ), user, db,
        )


@pytest.mark.asyncio
async def test_password_change_revokes_sessions_after_policy_validation(monkeypatch):
    user_id = uuid4()
    user = SimpleNamespace(
        id=user_id, hashed_password="old-hash", local_password_enabled=True,  # noqa: S106 - synthetic test fixture; not a production credential
        must_change_password=True,
    )
    db = SimpleNamespace(commits=0)
    revoked = []

    async def commit():
        db.commits += 1

    async def threadpool(func, *args):
        return func(*args)

    async def policy(_db):
        return {}

    async def history(*_args):
        return True

    async def add_history(*_args, **_kwargs):
        return None

    async def revoke(_db, user_id_to_revoke):
        revoked.append(user_id_to_revoke)

    db.commit = commit
    monkeypatch.setattr(auth_router, "run_in_threadpool", threadpool)
    monkeypatch.setattr(auth_router.security, "verify_password", lambda *_: True)
    monkeypatch.setattr(auth_router.security, "get_password_hash", lambda *_: "new-hash")
    monkeypatch.setattr(auth_router, "get_password_policy", policy)
    monkeypatch.setattr(auth_router.password_repo, "check_password_history", history)
    monkeypatch.setattr(auth_router.password_repo, "add_password_history", add_history)
    monkeypatch.setattr(auth_router, "revoke_user_authentication", revoke)

    await auth_router.self_change_password(
        _request(), ChangePasswordRequest(
            old_password="old", new_password="StrongPass!123",  # noqa: S106 - synthetic test fixture; not a production credential
            confirm_new_password="StrongPass!123",  # noqa: S106 - synthetic test fixture; not a production credential
        ), user, db,
    )
    assert user.hashed_password == "new-hash"  # noqa: S105 - synthetic test fixture; not a production credential
    assert revoked == [user_id]
    assert db.commits == 1


@pytest.mark.asyncio
async def test_admin_reset_rejects_out_of_scope_user_before_modification(monkeypatch):
    current_user = SimpleNamespace(id=uuid4(), role=SimpleNamespace(name="Admin"))
    db = SimpleNamespace(execute_calls=0)

    async def deny_scope(_db, _current_user, _user_id):
        raise HTTPException(status_code=403, detail="Access denied for this user's organizational scope.")

    monkeypatch.setattr(users_router, "enforce_permission", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(users_router, "enforce_user_scope", deny_scope)
    with pytest.raises(HTTPException) as error:
        await users_router.reset_user_password(
            uuid4(), PasswordResetAdmin(new_password="StrongPass!123"), db, current_user  # noqa: S106 - synthetic test fixture; not a production credential
        )
    assert error.value.status_code == 403
    assert db.execute_calls == 0


@pytest.mark.asyncio
async def test_super_admin_reset_revokes_existing_sessions(monkeypatch):
    user_id = uuid4()
    target = SimpleNamespace(
        id=user_id, role=SimpleNamespace(name="Department User"), is_deleted=False,
    )
    current_user = SimpleNamespace(id=uuid4(), role=SimpleNamespace(name="Super Admin"))
    revoked = []
    updates = []

    class DB:
        async def execute(self, _statement):
            return _Result(target)

    async def policy(_db):
        return {}

    async def history(*_args):
        return True

    async def update(_db, _user, values):
        updates.append(values)

    async def add_history(*_args, **_kwargs):
        return None

    async def revoke(_db, user_id_to_revoke):
        revoked.append(user_id_to_revoke)

    monkeypatch.setattr(users_router, "enforce_permission", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(users_router, "get_password_policy", policy)
    monkeypatch.setattr(users_router.password_repo, "check_password_history", history)
    monkeypatch.setattr(users_router.password_repo, "add_password_history", add_history)
    monkeypatch.setattr(users_router.user_repo, "update_user", update)
    monkeypatch.setattr(users_router, "get_password_hash", lambda *_: "new-hash")
    monkeypatch.setattr(users_router, "revoke_user_authentication", revoke)

    await users_router.reset_user_password(
        user_id,
        PasswordResetAdmin(new_password="StrongPass!123", confirm_new_password="StrongPass!123"),  # noqa: S106 - synthetic test fixture; not a production credential
        DB(),
        current_user,
    )
    assert updates == [{"hashed_password": "new-hash", "local_password_enabled": True, "must_change_password": True}]
    assert revoked == [user_id]


def test_response_models_do_not_expose_password_or_hash_fields():
    forbidden = {"password", "hashed_password", "password_hash", "reset_token"}
    assert not forbidden.intersection(UserResponse.model_fields)
    assert not forbidden.intersection(TokenResponse.model_fields)