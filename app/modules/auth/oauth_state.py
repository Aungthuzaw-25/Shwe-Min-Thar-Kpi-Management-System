import json
import secrets
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Request, status
from redis.asyncio import Redis

from app.core.config import settings


def get_oauth_redis(request: Request) -> Redis:
    if not settings.OAUTH_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="OAuth provider is not enabled.")
    client = getattr(request.app.state, "oauth_redis", None)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OAuth sign-in is temporarily unavailable.",
        )
    return client


async def create_oauth_state(
    redis: Redis,
    *,
    provider: str,
    nonce: str,
    pkce_verifier: str,
    mode: str = "login",
    user_id: str | None = None,
    return_path: str = "/dashboard",
    remember_me: bool = False,
) -> str:
    normalized_mode = mode.lower()
    if normalized_mode not in {"login", "link"}:
        raise HTTPException(status_code=400, detail="Invalid OAuth flow.")
    if normalized_mode == "link" and not user_id:
        raise HTTPException(status_code=400, detail="Authenticated user is required for account linking.")
    state = secrets.token_urlsafe(32)
    payload = {
        "provider": provider,
        "nonce": nonce,
        "pkce_verifier": pkce_verifier,
        "mode": normalized_mode,
        "flow_type": normalized_mode.upper(),
        "user_id": user_id,
        "remember_me": bool(remember_me),
        "return_path": return_path if return_path.startswith("/") and not return_path.startswith("//") else "/dashboard",
        "created_at": datetime.now(timezone.utc).isoformat(),
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
            detail="OAuth sign-in is temporarily unavailable.",
        ) from exc
    return state


async def consume_oauth_state(redis: Redis, state: str) -> dict[str, Any]:
    if not state or len(state) > 256:
        raise HTTPException(status_code=400, detail="Invalid OAuth state.")
    try:
        raw = await redis.getdel(f"oauth:state:{state}")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OAuth sign-in is temporarily unavailable.",
        ) from exc
    if not raw:
        raise HTTPException(status_code=400, detail="OAuth state is invalid or expired.")
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="OAuth state is invalid.") from exc
    return payload
