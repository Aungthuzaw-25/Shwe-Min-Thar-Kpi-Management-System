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

import asyncio
import logging
import secrets

from fastapi.encoders import jsonable_encoder
from redis.asyncio import from_url as redis_from_url

from app.core.config import settings

logger = logging.getLogger(__name__)

CACHE_PREFIX = "kpi:cache"

# Recompute locks live in their own namespace so a dashboard purge
# can never delete a live lock (2026-11-06, F2).
LOCK_PREFIX = "kpi:lock"
_LOCK_TTL_SECONDS = 10
_LOCK_WAIT_SECONDS = 5.0
_LOCK_POLL_INTERVAL = 0.05
_LOCK_UNAVAILABLE = object()

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

    Stampede lock (2026-11-06, F2): on a miss the first caller takes a
    short-lived per-key lock (``SET NX EX 10s`` in the ``kpi:lock``
    namespace); other concurrent misses poll the cache for up to 5s and
    share the result instead of recomputing. The lock is never released
    explicitly — it expires — so a crashed holder cannot wedge the key,
    and a waiter that times out (or any lock failure) simply computes
    fresh, exactly as before. The cache never breaks a request.
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
    lock_acquired = _LOCK_UNAVAILABLE
    if client is not None:
        try:
            lock_acquired = await client.set(
                f"{LOCK_PREFIX}:{prefix}:{key}",
                secrets.token_hex(8),
                nx=True,
                ex=_LOCK_TTL_SECONDS,
            )
        except Exception:
            # Lock unsupported or Redis broken — compute fresh, no wait.
            lock_acquired = _LOCK_UNAVAILABLE
    if lock_acquired is None:
        # Another worker holds the lock — wait for its write, then share it.
        waited = 0.0
        while waited < _LOCK_WAIT_SECONDS:
            await asyncio.sleep(_LOCK_POLL_INTERVAL)
            waited += _LOCK_POLL_INTERVAL
            try:
                raw = await client.get(cache_key)
            except Exception:
                break
            if raw is not None:
                return json.loads(raw)
        # Timed out (holder slow or crashed) — compute fresh below.
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


# The five cached dashboard endpoints, as the first segment of their
# cache keys (see _dashboard_cache_key in dashboard_router.py).
DASHBOARD_GROUPS = (
    "unified-summary",
    "executive-overview",
    "executive-monthly-averages",
    "admin-summary",
    "superadmin-summary",
)

# A task write changes task diagnostics/counts everywhere, but the
# monthly averages are pure KPI averages over finalized records —
# task status never changes them.
_TASK_WRITE_GROUPS = (
    "unified-summary",
    "executive-overview",
    "admin-summary",
    "superadmin-summary",
)

# Notification writes (marking one read, deleting one) change only the
# superadmin-summary group: it reads notifications (active_alerts = the
# unread, non-deleted count; recent_alerts = the 6 most recent unread,
# non-deleted). No other cached group reads notifications, so only
# that group is purged — the other four stay cached.
_NOTIFICATION_WRITE_GROUPS = ("superadmin-summary",)


def dashboard_groups_for_path(path: str) -> tuple[str, ...]:
    """Which cached dashboard groups a write to ``path`` can affect.

    Conservative by design: anything unlisted purges every group, so
    a future write path stays fresh by default and only the two
    proven-safe narrowings above skip a group. Pure function — easy
    to unit test without Redis.
    """
    if path.startswith("/api/v1/notifications/"):
        return _NOTIFICATION_WRITE_GROUPS
    if path.startswith("/api/v1/tasks/"):
        return _TASK_WRITE_GROUPS
    return DASHBOARD_GROUPS


async def _delete_exact_cache_key(key: str) -> int:
    """Best-effort delete of one exact key; 0 when absent or on error."""
    client = _get_client()
    if client is None:
        return 0
    try:
        return (await client.delete(key)) or 0
    except Exception:
        return 0


async def invalidate_dashboard_for_path(path: str) -> int:
    """Scoped purge for one write path (2026-11-06, F2).

    Deletes only the dashboard groups the write can affect (see
    :func:`dashboard_groups_for_path`) instead of the whole dashboard
    namespace for every viewer. Never raises.

    Each group is purged by prefix scan AND by exact key: the
    superadmin summary's cache key has no trailing segment
    (``kpi:cache:dashboard:superadmin-summary``), so the group scan
    alone would miss it.
    """
    deleted = 0
    for group in dashboard_groups_for_path(path):
        deleted += await invalidate_prefix(f"dashboard:{group}")
        deleted += await _delete_exact_cache_key(f"{CACHE_PREFIX}:dashboard:{group}")
    return deleted
