"""User presence for User Management, derived from existing auth data.

No schema change and no per-request writes: everything is computed
from rows the auth system already keeps.

Data sources:
- ``auth_sessions.last_used_at`` — set at login, updated when the
  client refreshes its token and on re-authentication.
- ``auth_sessions.revoked_at`` — set at logout, on admin revocation
  and on password change.
- ``auth_sessions.expires_at`` — when the session would die on its own.
- ``login_history.attempted_at`` (successful attempts) — logins.

Definitions (kept honest about what the data can really say):
- ``last_active_at``: the latest known activity — the newest of any
  session's ``last_used_at`` and the last successful login.
- ``presence_status`` is ``"online"`` when a session exists that is
  not revoked and not expired AND its ``last_used_at`` is inside the
  online window. While a user is really using the app, the client
  refreshes at least once per session-timeout period, which moves
  ``last_used_at``; activity older than the window therefore means the
  user has gone quiet. The window is the configured session timeout
  plus a small grace period. Anything else is ``"offline"``.
- ``last_offline_at``: when the user most recently went offline —
  the newest of the latest session revocation (an exact logout time)
  and ``last_active_at`` (for sessions that simply went quiet or
  expired, their last activity is the last time they were seen, so it
  is the honest offline estimate). ``None`` while the user is online,
  and ``None`` for a user who has never logged in.
- ``last_login_at``: the last successful login from ``login_history``.

Cost: exactly two aggregate queries (one per source table) for the
whole user list, no matter the page size — never one query per user.
The sessions aggregate rides the existing
``idx_auth_sessions_active_user`` index.
"""

import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.password_policy import get_session_timeout_minutes
from app.core.timezone import utc_now_naive
from app.modules.auth.models import AuthSession, LoginHistory

logger = logging.getLogger(__name__)

ONLINE_GRACE_MINUTES = 5
PRESENCE_ONLINE = "online"
PRESENCE_OFFLINE = "offline"


def compute_presence(
    *,
    last_session_activity: datetime | None,
    latest_revoked_at: datetime | None,
    live_last_used_at: datetime | None,
    last_login_at: datetime | None,
    now: datetime,
    online_window: timedelta,
) -> dict:
    """Pure presence calculation for one user (see module docstring)."""
    activity_candidates = [
        value for value in (last_session_activity, last_login_at) if value
    ]
    last_active_at = max(activity_candidates) if activity_candidates else None
    is_online = (
        live_last_used_at is not None
        and live_last_used_at >= now - online_window
    )
    if is_online:
        last_offline_at = None
    elif last_active_at is None:
        last_offline_at = None
    else:
        end_candidates = [
            value for value in (latest_revoked_at, last_active_at) if value
        ]
        last_offline_at = max(end_candidates)
    return {
        "presence_status": PRESENCE_ONLINE if is_online else PRESENCE_OFFLINE,
        "last_active_at": last_active_at,
        "last_offline_at": last_offline_at,
        "last_login_at": last_login_at,
    }


async def presence_for_users(
    db: AsyncSession,
    user_ids: list[UUID],
    *,
    now: datetime | None = None,
) -> dict[UUID, dict]:
    """Presence for many users with two aggregate queries total.

    Never raises: if the presence read fails, the user list must still
    work — callers simply get no entry (null presence) for that user.
    """
    if not user_ids:
        return {}
    now = now or utc_now_naive()
    try:
        timeout_minutes = await get_session_timeout_minutes(db)
        online_window = timedelta(minutes=timeout_minutes + ONLINE_GRACE_MINUTES)
        session_rows = (
            await db.execute(
                select(
                    AuthSession.user_id,
                    func.max(AuthSession.last_used_at),
                    func.max(AuthSession.revoked_at),
                    func.max(
                        case(
                            (
                                and_(
                                    AuthSession.revoked_at.is_(None),
                                    AuthSession.expires_at > now,
                                ),
                                AuthSession.last_used_at,
                            ),
                            else_=None,
                        )
                    ),
                )
                .where(AuthSession.user_id.in_(user_ids))
                .group_by(AuthSession.user_id)
            )
        ).all()
        login_rows = (
            await db.execute(
                select(
                    LoginHistory.user_id,
                    func.max(LoginHistory.attempted_at),
                )
                .where(
                    LoginHistory.user_id.in_(user_ids),
                    or_(
                        LoginHistory.is_success.is_(True),
                        LoginHistory.is_success.is_(None),
                    ),
                )
                .group_by(LoginHistory.user_id)
            )
        ).all()
    except Exception:
        logger.exception("User presence could not be computed.")
        return {}

    sessions = {
        user_id: (last_used, revoked, live_last_used)
        for user_id, last_used, revoked, live_last_used in session_rows
    }
    logins = {user_id: last_login for user_id, last_login in login_rows}
    result: dict[UUID, dict] = {}
    for user_id in user_ids:
        last_used, revoked, live_last_used = sessions.get(
            user_id, (None, None, None)
        )
        result[user_id] = compute_presence(
            last_session_activity=last_used,
            latest_revoked_at=revoked,
            live_last_used_at=live_last_used,
            last_login_at=logins.get(user_id),
            now=now,
            online_window=online_window,
        )
    return result
