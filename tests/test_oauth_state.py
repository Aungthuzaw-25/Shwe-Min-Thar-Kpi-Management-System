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


@pytest.mark.asyncio
async def test_oauth_state_is_one_time_and_provider_bound(monkeypatch):
    monkeypatch.setattr("app.modules.auth.oauth_state.settings.OAUTH_STATE_TTL_SECONDS", 600)
    redis = FakeRedis()
    state = await create_oauth_state(
        redis, provider="google", nonce="nonce", pkce_verifier="verifier"
    )
    payload = await consume_oauth_state(redis, state)
    assert json.loads(json.dumps(payload))["provider"] == "google"
    with pytest.raises(Exception):
        await consume_oauth_state(redis, state)


@pytest.mark.asyncio
async def test_oauth_state_rejects_expired_or_unknown_state():
    with pytest.raises(Exception):
        await consume_oauth_state(FakeRedis(), "unknown")
