"""Permission freshness markers (contract with the web tier).

Backend authorization is never stale: ``get_current_user`` reloads
the user, role permissions, and overrides from the database on every
request, so a role/permission change applies to the very next API
call. The one place staleness can live is the *web tier*, which
caches the ``/auth/me`` + ``/permissions/me`` context for 30 seconds
and cannot be reached by the backend.

These counters are the bridge: every write that can change a user's
effective permissions increments a per-user counter and/or a
per-role counter (Redis ``INCR``, fail-open like the rest of the
cache layer). ``/auth/me`` and ``/permissions/me`` expose the pair as
an opaque string, ``permissions_version`` (``"u<user>-r<role>"``).

Web-tier contract: treat the string as opaque. Cache the context
together with the version it was fetched with; whenever a freshly
observed version differs from the cached one, discard the cached
context and refetch. Equality means "unchanged". A Redis outage
reads as ``u0-r0`` and bumps become no-ops — enforcement never
depends on these counters, only cache freshness does, and a reset
counter reads as "changed" (refetch), never as "unchanged".
"""

from __future__ import annotations

import logging
from uuid import UUID

from app.core.cache import _get_client

logger = logging.getLogger(__name__)

_USER_KEY = "permver:user:{user_id}"
_ROLE_KEY = "permver:role:{role_id}"


async def _incr(key: str) -> None:
    client = _get_client()
    if client is None:
        return
    try:
        await client.incr(key)
    except Exception:
        logger.warning("Permission version bump failed for %s", key, exc_info=True)


async def _read(key: str) -> int:
    client = _get_client()
    if client is None:
        return 0
    try:
        value = await client.get(key)
        return int(value) if value is not None else 0
    except Exception:
        logger.warning("Permission version read failed for %s", key, exc_info=True)
        return 0


async def bump_user_permissions_version(user_id: UUID | str) -> None:
    """Call after any committed write that changes one user's
    role, position, department membership/level, or overrides."""
    await _incr(_USER_KEY.format(user_id=user_id))


async def bump_role_permissions_version(role_id: UUID | str) -> None:
    """Call after any committed write that changes a role's
    permission set (affects every member of the role)."""
    await _incr(_ROLE_KEY.format(role_id=role_id))


async def permissions_version_for(user) -> str:
    """The ``permissions_version`` string for /auth/me + /permissions/me."""
    user_version = await _read(_USER_KEY.format(user_id=user.id))
    role_id = getattr(user, "role_id", None) or (
        getattr(getattr(user, "role", None), "id", None)
    )
    role_version = (
        await _read(_ROLE_KEY.format(role_id=role_id)) if role_id else 0
    )
    return f"u{user_version}-r{role_version}"
