"""Dashboard aggregate caching (LCP Stage 1, Phase 1).

GET /api/v1/dashboard/summary, /executive/overview and
/executive/monthly-averages are cached 60s in the "dashboard" Redis
namespace via the existing cached_json helper. The key is endpoint +
viewer id + viewer role name + all query parameters sorted, so one
user's aggregate can never be served to another, and a role change
misses the old entry even before the write-path purge lands.

These tests drive the real routes (full app + TestClient, the proven
freshness pattern) with a counting wrapper around each build function:
a second identical call must return identical JSON without recomputing.
"""

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker

import app.core.cache as cache_module
from app.core import security
from app.database.connection import Base, get_db
import app.main as main_module
from app.modules.auth.models import AuthSession
from app.modules.kpi import dashboard_router
from app.modules.kpi.models import KPIPeriod, KPIPeriodStatus
from app.modules.users.models import Role, User


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value


class BrokenRedis:
    async def get(self, key):
        raise RuntimeError("redis down")

    async def set(self, key, value, ex=None):
        raise RuntimeError("redis down")


@pytest.fixture()
def world(tmp_path, monkeypatch):
    db_file = tmp_path / "dashcache.db"
    sync_engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(sync_engine)
    session = sessionmaker(sync_engine)()

    sa_role = Role(name="Super Admin")
    admin_role = Role(name="Admin")
    session.add_all([sa_role, admin_role])
    session.flush()
    period = KPIPeriod(
        label="June 2026",
        start_date=datetime(2026, 6, 1, tzinfo=timezone.utc),
        end_date=datetime(2026, 6, 30, 23, 59, tzinfo=timezone.utc),
        status=KPIPeriodStatus.OPEN,
        is_deleted=False,
    )
    session.add(period)
    session.flush()

    def make_user(email, role):
        user = User(
            email=email,
            hashed_password="x",
            first_name="T",
            last_name=email.split("@")[0],
            role_id=role.id,
            is_active=True,
        )
        session.add(user)
        session.flush()
        auth_session = AuthSession(
            user_id=user.id,
            refresh_token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            expires_at=datetime.utcnow() + timedelta(days=1),
            last_used_at=datetime.utcnow(),
        )
        session.add(auth_session)
        session.flush()
        token = security.create_access_token(
            {"sub": email, "user_id": str(user.id), "sid": str(auth_session.id)}
        )
        return user, token

    sa, sa_token = make_user("sa@example.com", sa_role)
    _, sa2_token = make_user("sa2@example.com", sa_role)
    ids = SimpleNamespace(sa=sa.id, sa_role=sa_role.id, admin_role=admin_role.id)
    session.commit()
    session.close()

    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    factory = async_sessionmaker(async_engine, expire_on_commit=False)

    async def override_get_db():
        async with factory() as db_session:
            yield db_session

    main_module.app.dependency_overrides[get_db] = override_get_db
    fake = FakeRedis()
    monkeypatch.setattr(cache_module, "_client", fake)
    monkeypatch.setattr(cache_module, "_client_unavailable", False)

    counts = {"summary": 0, "overview": 0, "monthly": 0}
    orig_summary = dashboard_router.build_dashboard_summary
    orig_overview = dashboard_router.build_executive_overview
    orig_monthly = dashboard_router.build_monthly_average_series

    async def counting_summary(*args, **kwargs):
        counts["summary"] += 1
        return await orig_summary(*args, **kwargs)

    async def counting_overview(*args, **kwargs):
        counts["overview"] += 1
        return await orig_overview(*args, **kwargs)

    async def counting_monthly(*args, **kwargs):
        counts["monthly"] += 1
        return await orig_monthly(*args, **kwargs)

    monkeypatch.setattr(dashboard_router, "build_dashboard_summary", counting_summary)
    monkeypatch.setattr(dashboard_router, "build_executive_overview", counting_overview)
    monkeypatch.setattr(
        dashboard_router, "build_monthly_average_series", counting_monthly
    )

    client = TestClient(main_module.app, raise_server_exceptions=False)
    w = SimpleNamespace(
        client=client, fake=fake, counts=counts, ids=ids,
        sa_token=sa_token, sa2_token=sa2_token, db_file=db_file,
    )
    yield w
    main_module.app.dependency_overrides.pop(get_db, None)


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


SUMMARY_QS = (
    "period_type=MONTHLY&period_start=2026-06-01T00%3A00%3A00Z"
    "&period_end=2026-07-01T00%3A00%3A00Z"
)


def test_summary_hit_identical_and_computed_once(world):
    url = f"/api/v1/dashboard/summary?{SUMMARY_QS}"
    first = world.client.get(url, headers=_headers(world.sa_token))
    assert first.status_code == 200, first.text
    second = world.client.get(url, headers=_headers(world.sa_token))
    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    assert world.counts["summary"] == 1


def test_summary_different_user_computes_separately(world):
    url = f"/api/v1/dashboard/summary?{SUMMARY_QS}"
    assert world.client.get(url, headers=_headers(world.sa_token)).status_code == 200
    assert world.client.get(url, headers=_headers(world.sa2_token)).status_code == 200
    assert world.counts["summary"] == 2
    # First user's entry is still cached: no third computation.
    assert world.client.get(url, headers=_headers(world.sa_token)).status_code == 200
    assert world.counts["summary"] == 2


def test_summary_role_change_misses_old_key(world):
    url = f"/api/v1/dashboard/summary?{SUMMARY_QS}"
    assert world.client.get(url, headers=_headers(world.sa_token)).status_code == 200
    assert world.counts["summary"] == 1
    sync_engine = create_engine(f"sqlite:///{world.db_file}")
    session = sessionmaker(sync_engine)()
    user = session.get(User, world.ids.sa)
    user.role_id = world.ids.admin_role
    session.commit()
    session.close()
    resp = world.client.get(url, headers=_headers(world.sa_token))
    assert resp.status_code == 200, resp.text
    assert world.counts["summary"] == 2


def test_summary_redis_broken_fails_open_to_fresh(world, monkeypatch):
    monkeypatch.setattr(cache_module, "_client", BrokenRedis())
    url = f"/api/v1/dashboard/summary?{SUMMARY_QS}"
    first = world.client.get(url, headers=_headers(world.sa_token))
    assert first.status_code == 200, first.text
    second = world.client.get(url, headers=_headers(world.sa_token))
    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    assert world.counts["summary"] == 2  # computed every time, never stale


def test_overview_hit_identical_and_computed_once(world):
    url = "/api/v1/dashboard/executive/overview"
    first = world.client.get(url, headers=_headers(world.sa_token))
    assert first.status_code == 200, first.text
    second = world.client.get(url, headers=_headers(world.sa_token))
    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    assert world.counts["overview"] == 1


def test_monthly_averages_hit_identical_and_computed_once(world):
    url = "/api/v1/dashboard/executive/monthly-averages?year=2026"
    first = world.client.get(url, headers=_headers(world.sa_token))
    assert first.status_code == 200, first.text
    second = world.client.get(url, headers=_headers(world.sa_token))
    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    assert world.counts["monthly"] == 1
