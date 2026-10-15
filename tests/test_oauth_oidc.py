import base64
import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Request
from fastapi.responses import Response

from app.modules.auth import router as auth_router
from app.modules.auth.oidc import (
    OIDCProvider,
    OIDCValidationError,
    create_nonce,
    create_pkce_challenge,
    create_pkce_verifier,
)


def _oidc_test_provider(monkeypatch):
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": "test-key", "use": "sig", "alg": "RS256"})
    provider = OIDCProvider(
        name="google",
        client_id="client-id",
        client_secret="client-secret",  # noqa: S106 - synthetic test fixture; not a production credential
        redirect_uri="https://app.example.com/callback",
        discovery_url="https://accounts.google.com/.well-known/openid-configuration",
        scopes=("openid", "email", "profile"),
    )

    async def metadata():
        return {
            "issuer": "https://accounts.google.com",
            "jwks_uri": "https://accounts.google.com/keys",
            "id_token_signing_alg_values_supported": ["RS256"],
        }

    async def jwks(_url):
        return {"keys": [jwk]}

    monkeypatch.setattr(provider, "metadata", metadata)
    monkeypatch.setattr(provider, "_jwks", jwks)
    return provider, private_key


def _signed_id_token(private_key, **claim_overrides):
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "sub": "subject-1",
        "aud": "client-id",
        "exp": now + 300,
        "iat": now,
        "nonce": "expected-nonce",
        "email": "alice@example.com",
        "email_verified": True,
        "name": "Alice Example",
    }
    claims.update(claim_overrides)
    return jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )


def test_pkce_verifier_and_challenge_are_generated():
    verifier = create_pkce_verifier()
    first_nonce = create_nonce()
    second_nonce = create_nonce()

    assert len(verifier) >= 43
    expected_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    assert create_pkce_challenge(verifier) == expected_challenge
    assert first_nonce != second_nonce


@pytest.mark.asyncio
async def test_google_callback_issuer_must_match_discovery_metadata(monkeypatch):
    provider = OIDCProvider(
        name="google",
        client_id="client-id",
        client_secret="client-secret",  # noqa: S106 - synthetic test fixture; not a production credential
        redirect_uri="https://app.example.com/api/v1/auth/google/callback",
        discovery_url="https://accounts.google.com/.well-known/openid-configuration",
        scopes=("openid", "email", "profile"),
    )

    async def metadata():
        return {"issuer": "https://accounts.google.com"}

    monkeypatch.setattr(provider, "metadata", metadata)
    await provider.validate_callback_issuer("https://accounts.google.com")

    with pytest.raises(OIDCValidationError):
        await provider.validate_callback_issuer("https://attacker.example")


@pytest.mark.asyncio
async def test_id_token_signature_and_expected_claims_are_validated(monkeypatch):
    provider, private_key = _oidc_test_provider(monkeypatch)
    identity = await provider.validate_identity(
        {"id_token": _signed_id_token(private_key)},
        "expected-nonce",
    )

    assert identity.subject == "subject-1"
    assert identity.email == "alice@example.com"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claims,nonce",
    [
        ({"iss": "https://attacker.example"}, "expected-nonce"),
        ({"aud": "another-client"}, "expected-nonce"),
        ({"exp": int(time.time()) - 10}, "expected-nonce"),
        ({"nonce": "another-nonce"}, "expected-nonce"),
        ({"aud": ["client-id", "another-client"]}, "expected-nonce"),
        ({}, "another-nonce"),
    ],
)
async def test_id_token_rejects_unexpected_issuer_audience_or_nonce(
    monkeypatch, claims, nonce
):
    provider, private_key = _oidc_test_provider(monkeypatch)
    id_token = _signed_id_token(private_key, **claims)

    with pytest.raises(OIDCValidationError):
        await provider.validate_identity({"id_token": id_token}, nonce)


@pytest.mark.asyncio
async def test_id_token_rejects_invalid_signature_and_algorithm(monkeypatch):
    provider, private_key = _oidc_test_provider(monkeypatch)
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    invalid_signature_token = _signed_id_token(other_key)

    with pytest.raises(OIDCValidationError):
        await provider.validate_identity(
            {"id_token": invalid_signature_token},
            "expected-nonce",
        )

    unsupported_token = jwt.encode(
        {"sub": "subject-1"},
        "not-a-trusted-key",
        algorithm="HS256",
        headers={"kid": "test-key"},
    )
    with pytest.raises(OIDCValidationError):
        await provider.validate_identity(
            {"id_token": unsupported_token},
            "expected-nonce",
        )

    unadvertised_algorithm_token = jwt.encode(
        {"sub": "subject-1"},
        private_key,
        algorithm="RS384",
        headers={"kid": "test-key"},
    )
    with pytest.raises(OIDCValidationError):
        await provider.validate_identity(
            {"id_token": unadvertised_algorithm_token},
            "expected-nonce",
        )


@pytest.mark.asyncio
async def test_id_token_rejects_malformed_compact_token(monkeypatch):
    provider, _private_key = _oidc_test_provider(monkeypatch)

    with pytest.raises(OIDCValidationError):
        await provider.validate_identity(
            {"id_token": "not.a.valid.jwt"},
            "expected-nonce",
        )


@pytest.mark.asyncio
async def test_shared_google_callback_dispatches_link_state_to_link_flow(monkeypatch):
    class Provider:
        async def validate_callback_issuer(self, callback_issuer):
            assert callback_issuer == "https://accounts.google.com"

    marker = object()
    calls = {}

    browser_binding = "A" * 43

    async def consume(_redis, _state, provided_binding):
        assert provided_binding == browser_binding
        return {
            "provider": "google",
            "flow_type": "LINK",
            "pkce_verifier": "verifier",
            "nonce": "nonce",
            "user_id": "superadmin-a",
            "session_id": "session-a",
        }

    async def complete_link(*args, **kwargs):
        calls["state_data"] = kwargs["state_data"]
        calls["current_user"] = args[6]
        return marker

    current_user = SimpleNamespace(
        id="superadmin-a",
        role=SimpleNamespace(name="Super Admin"),
    )

    async def authenticated_user(_request, _token, _db):
        return current_user

    monkeypatch.setattr(auth_router, "get_oauth_redis", lambda _request: object())
    monkeypatch.setattr(auth_router, "consume_oauth_state", consume)
    monkeypatch.setattr(auth_router, "_provider", lambda _name: Provider())
    monkeypatch.setattr(auth_router, "_complete_link", complete_link)
    monkeypatch.setattr(auth_router, "get_current_user", authenticated_user)
    monkeypatch.setattr(auth_router, "_request_session_id", lambda *_args: "session-a")

    request = SimpleNamespace(
        query_params={"iss": "https://accounts.google.com"},
        cookies={
            auth_router.OAUTH_BINDING_COOKIE_NAME: (
                f"{browser_binding}.{auth_router._oauth_binding_signature(browser_binding)}"
            ),
        },
    )
    result = await auth_router._complete_oauth_login(
        request,
        object(),
        "google",
        "authorization-code",
        "state",
        None,
        "https://accounts.google.com",
    )

    assert result is marker
    assert calls["state_data"]["flow_type"] == "LINK"
    assert calls["current_user"] is current_user


def test_oauth_browser_binding_cookie_rejects_forged_value():
    request = SimpleNamespace(
        cookies={
            auth_router.OAUTH_BINDING_COOKIE_NAME: f"{'A' * 43}.forged",
        }
    )

    binding, cookie_value = auth_router._oauth_browser_binding(request)

    assert len(binding) == 43
    assert binding != "A" * 43
    assert cookie_value.endswith(auth_router._oauth_binding_signature(binding))


def test_link_flow_auth_cookies_remain_usable_after_oauth_redirect():
    response = Response()
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": "/api/v1/auth/google/callback",
            "headers": [(b"host", b"kpi.example.com")],
            "query_string": b"",
            "server": ("kpi.example.com", 443),
            "client": ("127.0.0.1", 1),
        }
    )

    auth_router._set_auth_cookies(
        response,
        request,
        {"access_token": "access", "refresh_token": "refresh"},
        remember_me=True,
    )

    cookies = "\n".join(response.headers.getlist("set-cookie"))
    assert "access_token=access" in cookies
    assert "refresh_token=refresh" in cookies
    assert "HttpOnly" in cookies
    assert "Secure" in cookies
    assert "SameSite=lax" in cookies
    assert "Max-Age=0" not in cookies


@pytest.mark.asyncio
async def test_provider_reauthentication_forces_interactive_prompt_and_max_age():
    provider = OIDCProvider(
        name="google",
        client_id="client",
        client_secret="secret",
        redirect_uri="https://app.test/callback",
        discovery_url="https://accounts.google.com/.well-known/openid-configuration",
        scopes=("openid", "email", "profile"),
    )

    async def metadata():
        return {"authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth"}

    provider.metadata = metadata
    url = await provider.authorization_url(
        "state", "nonce", "verifier", force_reauthentication=True
    )
    query = parse_qs(urlparse(url).query)

    assert query["prompt"] == ["login"]
    assert query["max_age"] == ["0"]


@pytest.mark.asyncio
async def test_provider_reauthentication_requires_fresh_auth_time():
    provider = OIDCProvider(
        name="google",
        client_id="client",
        client_secret="secret",
        redirect_uri="https://app.test/callback",
        discovery_url="https://accounts.google.com/.well-known/openid-configuration",
        scopes=("openid", "email", "profile"),
    )

    async def metadata():
        return {"issuer": "https://accounts.google.com"}

    async def claims(_token, _metadata):
        return {
            "auth_time": (datetime.now(UTC) - timedelta(minutes=2)).timestamp(),
            "nonce": "nonce",
            "sub": "provider-subject",
            "email": "user@example.com",
            "email_verified": True,
            "name": "Test User",
        }

    provider.metadata = metadata
    provider._decode_identity_claims = claims
    identity = await provider.validate_identity(
        {"id_token": "token"},
        "nonce",
        require_recent_authentication=True,
    )
    assert identity.subject == "provider-subject"

    async def stale_claims(_token, _metadata):
        return {
            "auth_time": (datetime.now(UTC) - timedelta(minutes=11)).timestamp(),
            "nonce": "nonce",
            "sub": "provider-subject",
            "email": "user@example.com",
            "email_verified": True,
        }

    provider._decode_identity_claims = stale_claims
    with pytest.raises(OIDCValidationError, match="not recent"):
        await provider.validate_identity(
            {"id_token": "token"},
            "nonce",
            require_recent_authentication=True,
        )
