import json

import pytest

from app.modules.auth.oauth_state import consume_oauth_state, create_oauth_state


class FakeRedis:
    def __init__(self):
        self.values = {}

    async def set(self, key, value, ex, nx):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def getdel(self, key):
        return self.values.pop(key, None)

    async def get(self, key):
        return self.values.get(key)


@pytest.mark.asyncio
async def test_oauth_state_is_one_time_and_provider_bound(monkeypatch):
    monkeypatch.setattr("app.modules.auth.oauth_state.settings.OAUTH_STATE_TTL_SECONDS", 600)
    redis = FakeRedis()
    state = await create_oauth_state(
        redis,
        provider="google",
        nonce="nonce",
        pkce_verifier="verifier",
        browser_binding="browser-secret",
    )
    payload = await consume_oauth_state(redis, state, "browser-secret")
    assert json.loads(json.dumps(payload))["provider"] == "google"
    with pytest.raises(Exception):
        await consume_oauth_state(redis, state, "browser-secret")


@pytest.mark.asyncio
async def test_oauth_state_rejects_expired_or_unknown_state():
    with pytest.raises(Exception):
        await consume_oauth_state(FakeRedis(), "unknown", "browser-secret")


@pytest.mark.asyncio
async def test_oauth_state_distinguishes_login_and_link_flows():
    redis = FakeRedis()
    login_state = await create_oauth_state(
        redis,
        provider="google",
        nonce="login-nonce",
        pkce_verifier="login-verifier",
        browser_binding="login-browser",
    )
    link_state = await create_oauth_state(
        redis,
        provider="google",
        nonce="link-nonce",
        pkce_verifier="link-verifier",
        browser_binding="link-browser",
        mode="link",
        user_id="user-a",
        session_id="session-a",
    )

    login_payload = await consume_oauth_state(redis, login_state, "login-browser")
    link_payload = await consume_oauth_state(redis, link_state, "link-browser")

    assert login_payload["flow_type"] == "LOGIN"
    assert login_payload["user_id"] is None
    assert link_payload["flow_type"] == "LINK"
    assert link_payload["user_id"] == "user-a"


@pytest.mark.asyncio
async def test_link_state_requires_an_authenticated_user():
    with pytest.raises(Exception):
        await create_oauth_state(
            FakeRedis(),
            provider="microsoft",
            nonce="nonce",
            pkce_verifier="verifier",
            browser_binding="browser-secret",
            mode="link",
        )


@pytest.mark.asyncio
async def test_oauth_state_rejects_callback_from_a_different_browser_without_consuming_state():
    redis = FakeRedis()
    state = await create_oauth_state(
        redis,
        provider="google",
        nonce="nonce",
        pkce_verifier="verifier",
        browser_binding="initiating-browser",
    )

    with pytest.raises(Exception):
        await consume_oauth_state(redis, state, "attacker-browser")

    payload = await consume_oauth_state(redis, state, "initiating-browser")
    assert payload["provider"] == "google"
