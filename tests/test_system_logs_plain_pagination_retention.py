"""System Logs: plain-language display, pagination, retention cap.

Acceptance (Zenn, 2026-10-23):
- With >= 1,000 system-log rows seeded, a prune deletes EXACTLY the
  oldest 100 (down to the 900 target) and never touches audit_logs.
- Pagination can walk the entire remaining set exactly once.
- A raw technical error row's default display contains no raw error
  code, exception name, or stack text — while support can still
  reach the raw text via the detail endpoint's technical_message.
"""

import asyncio
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

import app.main as main_module
from app.core.dependencies import get_current_user
from app.database.connection import Base, get_db
from app.models.audit_log import AuditLog
from app.models.system import LogLevel, SystemEventLog
from app.modules.users.models import Role, User
from app.services.system_log_service import prune_system_event_logs

RAW_MESSAGE = (
    'psycopg2.errors.UniqueViolation: duplicate key value violates '
    'unique constraint "users_email_key"\n'
    "DETAIL:  Key (email)=(a@b.com) already exists.\n"
    'File "/app/main.py", line 88, in run'
)


def _seed_logs(sync_session, count, *, start=datetime(2026, 1, 1)):
    rows = []
    for i in range(count):
        rows.append(
            SystemEventLog(
                event_type="EMAIL_SYNC",
                level=LogLevel.INFO,
                message=f"sync batch {i}",
                details={"component": "email", "batch": i},
                created_at=start + timedelta(minutes=i),
            )
        )
    sync_session.add_all(rows)
    sync_session.commit()
    return [row.id for row in rows]


@pytest.fixture()
def db_world(tmp_path):
    db_file = tmp_path / "logs.db"
    sync_engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(sync_engine)
    sync_session = sessionmaker(sync_engine)()
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    yield SimpleNamespace(session=sync_session, factory=factory, file=db_file)
    sync_session.close()


def _run(factory, coro_fn):
    async def _main():
        async with factory() as db:
            return await coro_fn(db)

    with asyncio.Runner() as runner:
        return runner.run(_main())


def test_prune_deletes_exactly_the_oldest_down_to_900(db_world):
    ids = _seed_logs(db_world.session, 1005)
    db_world.session.add_all(
        [
            AuditLog(user_id=uuid.uuid4(), action="LOGIN", table_name="users")
            for _ in range(3)
        ]
    )
    db_world.session.commit()

    deleted = _run(db_world.factory, prune_system_event_logs)
    assert deleted == 105  # 1005 -> 900: the oldest 105 go

    remaining = (
        db_world.session.execute(
            select(SystemEventLog.id).order_by(SystemEventLog.created_at)
        )
        .scalars()
        .all()
    )
    assert len(remaining) == 900
    assert set(remaining) == set(ids[105:])  # exactly the newest 900 stay
    # audit_logs are a compliance trail: untouched.
    audit_count = db_world.session.execute(
        select(func.count(AuditLog.id))
    ).scalar_one()
    assert audit_count == 3
    # Idempotent: a second run (and a run below the cap) deletes nothing.
    assert _run(db_world.factory, prune_system_event_logs) == 0


def _client_with_logs(db_world):
    async def override_get_db():
        async with db_world.factory() as db:
            yield db

    sa_user = User(
        id=uuid.uuid4(),
        email="sa@x.com",
        hashed_password="x",
        first_name="S",
        last_name="A",
        role=Role(name="Super Admin"),
        is_active=True,
    )

    async def override_user():
        return sa_user

    main_module.app.dependency_overrides[get_db] = override_get_db
    main_module.app.dependency_overrides[get_current_user] = override_user
    return TestClient(main_module.app, raise_server_exceptions=False)


@pytest.fixture()
def api_world(db_world):
    yield db_world
    main_module.app.dependency_overrides.pop(get_db, None)
    main_module.app.dependency_overrides.pop(get_current_user, None)


def test_pagination_walks_the_whole_set_exactly_once(api_world):
    ids = _seed_logs(api_world.session, 230)
    client = _client_with_logs(api_world)
    seen = []
    page = 1
    while True:
        response = client.get(
            "/api/v1/system/logs", params={"page": page, "page_size": 100}
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 230
        assert payload["page"] == page
        assert payload["page_size"] == 100
        seen.extend(item["id"] for item in payload["items"])
        if len(seen) >= 230:
            break
        page += 1
    assert len(seen) == 230
    assert set(seen) == {str(i) for i in ids}  # every row, exactly once


def test_default_display_is_plain_and_detail_keeps_raw_for_support(api_world):
    raw = SystemEventLog(
        event_type="EMAIL_SYNC",
        level=LogLevel.ERROR,
        message=RAW_MESSAGE,
        details={"exception_type": "UniqueViolation", "component": "email"},
        created_at=datetime(2026, 1, 2),
    )
    friendly = SystemEventLog(
        event_type="EMAIL_SYNC",
        level=LogLevel.INFO,
        message="Email sync finished for 12 accounts",
        details={"component": "email"},
        created_at=datetime(2026, 1, 3),
    )
    api_world.session.add_all([raw, friendly])
    api_world.session.commit()
    client = _client_with_logs(api_world)

    listing = client.get("/api/v1/system/logs", params={"page_size": 10})
    assert listing.status_code == 200
    items = {item["id"]: item for item in listing.json()["items"]}
    raw_item = items[str(raw.id)]
    for marker in ("UniqueViolation", "psycopg", "duplicate key", "line 88", "Traceback"):
        assert marker not in raw_item["message"]
    assert "support team" in raw_item["message"]  # plain business wording
    assert "exception_type" not in (raw_item["details"] or {})
    assert "technical_message" not in raw_item  # list never carries raw text
    # A genuinely plain stored message passes through unchanged.
    assert items[str(friendly.id)]["message"] == "Email sync finished for 12 accounts"

    detail = client.get(f"/api/v1/system/logs/{raw.id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["message"] == raw_item["message"]  # still plain by default
    assert body["technical_message"] == RAW_MESSAGE  # support can reach the raw text
