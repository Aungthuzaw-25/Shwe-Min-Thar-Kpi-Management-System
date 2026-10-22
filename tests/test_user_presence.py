"""User presence in User Management (backend stage 1).

Presence is derived from data the auth system already keeps:
auth_sessions (last_used_at / revoked_at / expires_at) and
login_history (successful logins). No schema change, no extra writes.

Covered here:
- Online: live session with recent activity -> online, active time set,
  offline time null.
- Offline after logout: the revocation time is the offline time.
- Offline after going quiet / expiring: last activity is the offline
  estimate.
- Never logged in: offline with clean nulls.
- The list endpoint fills the fields with a constant number of extra
  queries and refuses callers without the users.view permission.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.exceptions import PermissionDeniedException
from app.modules.users import router as users_router
from app.modules.users.presence import compute_presence, presence_for_users

NOW = datetime(2026, 10, 22, 12, 0, 0)
WINDOW = timedelta(minutes=35)  # default 30 min session timeout + 5 grace


def _presence(**overrides):
    base = {
        "last_session_activity": None,
        "latest_revoked_at": None,
        "live_last_used_at": None,
        "last_login_at": None,
        "now": NOW,
        "online_window": WINDOW,
    }
    base.update(overrides)
    return compute_presence(**base)


# ---------------------------------------------------------------------------
# Pure presence rules
# ---------------------------------------------------------------------------

def test_online_user_with_fresh_live_session():
    result = _presence(
        last_session_activity=NOW - timedelta(minutes=4),
        live_last_used_at=NOW - timedelta(minutes=4),
        last_login_at=NOW - timedelta(hours=2),
    )
    assert result["presence_status"] == "online"
    assert result["last_active_at"] == NOW - timedelta(minutes=4)
    assert result["last_offline_at"] is None
    assert result["last_login_at"] == NOW - timedelta(hours=2)


def test_logged_out_user_is_offline_with_revocation_as_offline_time():
    result = _presence(
        last_session_activity=NOW - timedelta(hours=3),
        latest_revoked_at=NOW - timedelta(hours=2),
        last_login_at=NOW - timedelta(hours=3),
    )
    assert result["presence_status"] == "offline"
    assert result["last_active_at"] == NOW - timedelta(hours=3)
    assert result["last_offline_at"] == NOW - timedelta(hours=2)


def test_quiet_user_with_live_but_stale_session_is_offline():
    # Session not revoked and not expired, but no activity inside the
    # window: the user closed the app and went quiet.
    result = _presence(
        last_session_activity=NOW - timedelta(hours=5),
        live_last_used_at=NOW - timedelta(hours=5),
    )
    assert result["presence_status"] == "offline"
    assert result["last_offline_at"] == NOW - timedelta(hours=5)


def test_user_who_never_logged_in_has_clean_nulls():
    result = _presence()
    assert result == {
        "presence_status": "offline",
        "last_active_at": None,
        "last_offline_at": None,
        "last_login_at": None,
    }


def test_login_history_alone_counts_as_activity():
    result = _presence(last_login_at=NOW - timedelta(days=2))
    assert result["presence_status"] == "offline"
    assert result["last_active_at"] == NOW - timedelta(days=2)
    assert result["last_offline_at"] == NOW - timedelta(days=2)


# ---------------------------------------------------------------------------
# Aggregate loading (two queries for any number of users)
# ---------------------------------------------------------------------------

class FakeResult:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def unique(self):
        return self


class FakePresenceDB:
    """Routes statements to canned rows by the table they read."""

    def __init__(self, *, session_rows=(), login_rows=(), users=()):
        self.session_rows = session_rows
        self.login_rows = login_rows
        self.users = users
        self.queries = 0

    async def execute(self, stmt):
        self.queries += 1
        text = str(stmt)
        if "auth_sessions" in text:
            return FakeResult(self.session_rows)
        if "login_history" in text:
            return FakeResult(self.login_rows)
        if "system_settings" in text:
            return FakeResult([None])
        return FakeResult(self.users)


async def test_presence_for_users_aggregates_both_sources():
    online_id, offline_id, new_id = uuid4(), uuid4(), uuid4()
    db = FakePresenceDB(
        session_rows=[
            (
                online_id,
                NOW - timedelta(minutes=4),
                None,
                NOW - timedelta(minutes=4),
            ),
            (
                offline_id,
                NOW - timedelta(hours=3),
                NOW - timedelta(hours=2),
                None,
            ),
        ],
        login_rows=[(online_id, NOW - timedelta(hours=2))],
    )
    result = await presence_for_users(
        db, [online_id, offline_id, new_id], now=NOW
    )
    assert result[online_id]["presence_status"] == "online"
    assert result[online_id]["last_login_at"] == NOW - timedelta(hours=2)
    assert result[offline_id]["presence_status"] == "offline"
    assert result[offline_id]["last_offline_at"] == NOW - timedelta(hours=2)
    assert result[new_id]["presence_status"] == "offline"
    assert result[new_id]["last_active_at"] is None
    # One settings read (session timeout) + one sessions aggregate +
    # one login aggregate — independent of how many users were asked.
    assert db.queries == 3


async def test_presence_for_users_without_ids_runs_no_queries():
    db = FakePresenceDB()
    assert await presence_for_users(db, [], now=NOW) == {}
    assert db.queries == 0


# ---------------------------------------------------------------------------
# User list / detail endpoints
# ---------------------------------------------------------------------------

def _listed_user(user_id, email):
    return SimpleNamespace(
        id=user_id,
        first_name="Test",
        last_name="User",
        email=email,
        is_active=True,
        role=SimpleNamespace(id=uuid4(), name="Employee"),
        position=None,
        user_departments=[],
        region_town=None,
        created_at=NOW - timedelta(days=30),
        avatar_path=None,
        avatar_updated_at=None,
    )


def _caller(role_name="Super Admin"):
    return SimpleNamespace(
        id=uuid4(),
        role=SimpleNamespace(name=role_name, permissions=[]),
        permission_overrides=[],
    )


async def test_user_list_includes_presence_fields():
    online_id = uuid4()
    listed = _listed_user(online_id, "online@example.com")
    db = FakePresenceDB(
        session_rows=[
            (
                online_id,
                NOW - timedelta(minutes=1),
                None,
                NOW - timedelta(minutes=1),
            )
        ],
        login_rows=[(online_id, NOW - timedelta(hours=1))],
        users=[listed],
    )
    result = await users_router.get_users(
        is_active=True, skip=0, limit=100, db=db, current_user=_caller()
    )
    assert len(result) == 1
    user = result[0]
    assert user.presence_status in {"online", "offline"}
    assert user.last_active_at is not None
    assert user.last_login == NOW - timedelta(hours=1)


async def test_user_list_without_permission_is_refused_before_any_query():
    db = FakePresenceDB()
    with pytest.raises(PermissionDeniedException):
        await users_router.get_users(
            is_active=True,
            skip=0,
            limit=100,
            db=db,
            current_user=_caller("Employee"),
        )
    assert db.queries == 0


def test_serialize_user_obj_maps_presence_and_defaults():
    user_id = uuid4()
    user = _listed_user(user_id, "plain@example.com")
    plain = users_router.serialize_user_obj(user)
    assert plain.presence_status is None
    assert plain.last_active_at is None
    assert plain.last_offline_at is None

    enriched = users_router.serialize_user_obj(
        user,
        {
            "presence_status": "offline",
            "last_active_at": NOW - timedelta(hours=3),
            "last_offline_at": NOW - timedelta(hours=2),
            "last_login_at": NOW - timedelta(hours=3),
        },
    )
    assert enriched.presence_status == "offline"
    assert enriched.last_active_at == NOW - timedelta(hours=3)
    assert enriched.last_offline_at == NOW - timedelta(hours=2)
    assert enriched.last_login == NOW - timedelta(hours=3)
