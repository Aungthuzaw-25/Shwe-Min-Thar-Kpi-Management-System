from types import SimpleNamespace

import pytest
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


def test_pkce_verifier_and_challenge_are_generated():
    verifier = create_pkce_verifier()
    assert len(verifier) >= 43
    assert create_pkce_challenge(verifier)
    assert create_nonce() != create_nonce()


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
async def test_shared_google_callback_dispatches_link_state_to_link_flow(monkeypatch):
    class Provider:
        async def validate_callback_issuer(self, callback_issuer):
            assert callback_issuer == "https://accounts.google.com"

    marker = object()
    calls = {}

    async def consume(_redis, _state):
        return {
            "provider": "google",
            "flow_type": "LINK",
            "pkce_verifier": "verifier",
            "nonce": "nonce",
        }

    async def complete_link(*args, **kwargs):
        calls["state_data"] = kwargs["state_data"]
        calls["current_user"] = args[6]
        return marker

    monkeypatch.setattr(auth_router, "get_oauth_redis", lambda _request: object())
    monkeypatch.setattr(auth_router, "consume_oauth_state", consume)
    monkeypatch.setattr(auth_router, "_provider", lambda _name: Provider())
    monkeypatch.setattr(auth_router, "_complete_link", complete_link)

    request = SimpleNamespace(query_params={"iss": "https://accounts.google.com"})
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
    assert calls["current_user"] is None


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
