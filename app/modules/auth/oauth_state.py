import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, Request, status
from redis.asyncio import Redis

from app.core.config import settings

OAUTH_UNAVAILABLE_DETAIL = "OAuth sign-in is temporarily unavailable."
INVALID_OAUTH_STATE_MESSAGE = "Invalid OAuth state."
OAUTH_BINDING_COOKIE_NAME = "oauth_browser_binding"


def get_oauth_redis(request: Request) -> Redis:
    if not settings.OAUTH_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OAuth provider is not enabled.")
    client = getattr(request.app.state, "oauth_redis", None)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=OAUTH_UNAVAILABLE_DETAIL,
        )
    return client


async def create_oauth_state(
    redis: Redis,
    *,
    provider: str,
    nonce: str,
    pkce_verifier: str,
    browser_binding: str,
    mode: str = "login",
    user_id: str | None = None,
    session_id: str | None = None,
    return_path: str = "/dashboard",
    remember_me: bool = False,
) -> str:
    normalized_mode = mode.lower()
    if normalized_mode not in {"login", "link"}:
        raise HTTPException(status_code=400, detail="Invalid OAuth flow.")
    if not browser_binding or len(browser_binding) > 256:
        raise HTTPException(status_code=400, detail="Invalid OAuth browser binding.")
    if normalized_mode in {"link", "reauth"} and (not user_id or not session_id):
        raise HTTPException(status_code=400, detail="Authenticated session is required for this OAuth flow.")
    state = secrets.token_urlsafe(32)
    payload = {
        "provider": provider,
        "nonce": nonce,
        "pkce_verifier": pkce_verifier,
        "browser_binding_hash": hashlib.sha256(browser_binding.encode("utf-8")).hexdigest(),
        "mode": normalized_mode,
        "flow_type": normalized_mode.upper(),
        "user_id": user_id,
        "session_id": session_id,
        "remember_me": bool(remember_me),
        "return_path": return_path if return_path.startswith("/") and not return_path.startswith("//") else "/dashboard",
        "created_at": datetime.now(UTC).isoformat(),
    }
    try:
        await redis.set(
            f"oauth:state:{state}",
            json.dumps(payload),
            ex=settings.OAUTH_STATE_TTL_SECONDS,
            nx=True,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=OAUTH_UNAVAILABLE_MESSAGE,
        ) from exc
    return state


async def consume_oauth_state(
    redis: Redis,
    state: str,
    browser_binding: str | None,
) -> dict[str, Any]:
    if not state or len(state) > 256:
        raise HTTPException(status_code=400, detail=INVALID_OAUTH_STATE_MESSAGE)
    try:
        key = f"oauth:state:{state}"
        raw = await redis.get(key)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=OAUTH_UNAVAILABLE_DETAIL,
        ) from exc
    if not raw:
        raise HTTPException(status_code=400, detail="OAuth state is invalid or expired.")
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=INVALID_OAUTH_STATE_MESSAGE) from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail=INVALID_OAUTH_STATE_MESSAGE)
    expected_binding = payload.get("browser_binding_hash")
    if (
        not browser_binding
        or not isinstance(expected_binding, str)
        or not hmac.compare_digest(
            expected_binding,
            hashlib.sha256(browser_binding.encode("utf-8")).hexdigest(),
        )
    ):
        raise HTTPException(status_code=400, detail=INVALID_OAUTH_STATE_MESSAGE)
    try:
        consumed = await redis.getdel(key)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=OAUTH_UNAVAILABLE_DETAIL,
        ) from exc
    if not consumed:
        raise HTTPException(status_code=400, detail="OAuth state is invalid or expired.")
    return payload
