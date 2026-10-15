from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.modules.auth.router import _link_start, _resolve_oauth_user, _unlink_provider
from app.modules.users.models import OAuthAccount


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return self

    def first(self):
        return self.value


class _QueuedSession:
    def __init__(self, *results):
        self.results = list(results)

    async def execute(self, _statement):
        return _ScalarResult(self.results.pop(0))


def _identity(provider, subject, email):
    return SimpleNamespace(
        provider=provider,
        issuer=f"https://issuer.{provider}.example",
        tenant_id="tenant-a" if provider == "microsoft" else None,
        subject=subject,
        email=email,
    )


@pytest.mark.asyncio
async def test_unknown_oauth_identity_is_not_provisioned_or_auto_linked():
    db = _QueuedSession(None)

    user, identity_row = await _resolve_oauth_user(
        db, _identity("google", "GOOGLE_123", "aung@company.com")
    )

    assert user is None
    assert identity_row is None
    assert db.results == []


@pytest.mark.asyncio
async def test_same_email_does_not_link_an_unlinked_provider_identity():
    db = _QueuedSession(None)

    user, identity_row = await _resolve_oauth_user(
        db, _identity("microsoft", "MS_456", "aung@company.com")
    )

    assert user is None
    assert identity_row is None


@pytest.mark.asyncio
async def test_linked_identity_resolves_the_existing_internal_user():
    identity_row = SimpleNamespace(user_id="user-a")
    internal_user = SimpleNamespace(id="user-a", email="aung@company.com")
    db = _QueuedSession(identity_row, internal_user)

    user, resolved_identity = await _resolve_oauth_user(
        db, _identity("google", "GOOGLE_123", "aung@company.com")
    )

    assert user is internal_user
    assert resolved_identity is identity_row


def test_oauth_accounts_is_the_single_canonical_oauth_table():
    table = OAuthAccount.__table__

    assert table.name == "oauth_accounts"
    assert {
        "user_id",
        "provider",
        "issuer",
        "tenant_id",
        "provider_subject",
        "provider_email",
        "email_verified",
        "linked_at",
        "last_login_at",
        "revoked_at",
    }.issubset(table.c.keys())
    assert any(
        constraint.name == "uq_oauth_account_provider_subject"
        for constraint in table.constraints
    )


@pytest.mark.asyncio
async def test_only_super_admin_can_start_provider_linking():
    user = SimpleNamespace(role=SimpleNamespace(name="Admin"))

    with pytest.raises(HTTPException) as error:
        await _link_start(object(), "google", user, object(), object())

    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_only_super_admin_can_unlink_provider_identity():
    user = SimpleNamespace(id="user-a", role=SimpleNamespace(name="Admin"))

    with pytest.raises(HTTPException) as error:
        await _unlink_provider(object(), user, "google", object())

    assert error.value.status_code == 403
