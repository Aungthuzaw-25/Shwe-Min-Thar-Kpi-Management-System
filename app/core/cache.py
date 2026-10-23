"""Small Redis-backed JSON cache for expensive, read-heavy aggregates.

Batch 2 performance work. Design rules:

- Uses the same ``REDIS_URL`` the app already configures for OAuth state,
  but only ever touches its own ``kpi:cache:`` namespace. OAuth keys are
  security-sensitive transient state and are never read or deleted here.
- Fail-open: when Redis is not configured or not reachable, callers compute
  fresh values. The cache must never break a request.
- Freshness is bounded by short TTLs at the call sites (60s for dashboard
  summaries). :func:`invalidate_prefix` powers explicit purges such as
  System Settings -> Clear Cache.
"""

import logging

from fastapi.encoders import jsonable_encoder
from redis.asyncio import from_url as redis_from_url

from app.core.config import settings

logger = logging.getLogger(__name__)

CACHE_PREFIX = "kpi:cache"

_client = None
_client_unavailable = False


def _get_client():
    global _client, _client_unavailable
    if _client is not None:
        return _client
    if _client_unavailable or not settings.REDIS_URL:
        return None
    try:
        _client = redis_from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=1.0,
        )
    except Exception:  # pragma: no cover - defensive: cache must never break the app
        logger.warning("App cache: could not create Redis client", exc_info=True)
        _client_unavailable = True
        return None
    return _client


async def cache_available() -> bool:
    """True only when Redis is configured *and* answers a ping."""
    client = _get_client()
    if client is None:
        return False
    try:
        return bool(await client.ping())
    except Exception:
        return False


async def cached_json(prefix: str, key: str, ttl_seconds: int, factory):
    """Return the cached value for ``kpi:cache:<prefix>:<key>``.

    On a miss (or any cache error) ``factory()`` is awaited, its result is
    stored JSON-encoded exactly the way FastAPI would encode the response,
    and returned.
    """
    import json

    client = _get_client()
    cache_key = f"{CACHE_PREFIX}:{prefix}:{key}"
    if client is not None:
        try:
            raw = await client.get(cache_key)
            if raw is not None:
                return json.loads(raw)
        except Exception:
            logger.warning("App cache read failed; computing fresh value", exc_info=True)
    value = await factory()
    if client is not None:
        try:
            await client.set(
                cache_key,
                json.dumps(jsonable_encoder(value)),
                ex=ttl_seconds,
            )
        except Exception:
            logger.warning("App cache write failed", exc_info=True)
    return value


async def invalidate_prefix(prefix: str) -> int:
    """Delete every key under ``kpi:cache:<prefix>:*``; return the count.

    Fail-open like the rest of this module: a cache purge must never
    break the request that triggered it, so Redis errors are logged and
    the count deleted so far (possibly 0) is returned.
    """
    client = _get_client()
    if client is None:
        return 0
    deleted = 0
    try:
        async for key in client.scan_iter(
            match=f"{CACHE_PREFIX}:{prefix}:*", count=200
        ):
            deleted += await client.delete(key)
    except Exception:
        logger.warning(
            "App cache invalidation for prefix %r failed; continuing",
            prefix,
            exc_info=True,
        )
    return deleted


async def invalidate_dashboard_summaries() -> int:
    """Best-effort purge of the cached dashboard summaries.

    Called after successful data-changing requests so admin/superadmin
    dashboards stop showing pre-change numbers immediately instead of
    at the 60s TTL. Never raises.
    """
    return await invalidate_prefix("dashboard")
