"""GET /kpi/periods?year= — server-side year filter for the year view.

The filter matches the period's start_date calendar year in the
application timezone (period boundaries are local midnights), and
omitting the parameter must not change existing behaviour.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.timezone import APPLICATION_TIMEZONE
from app.modules.kpi.models import KPIPeriod, KPIPeriodStatus
from app.modules.kpi.period_router import get_kpi_periods
from app.modules.users.models import Role


def _period(label, start):
    return KPIPeriod(
        label=label,
        start_date=start,
        end_date=start + timedelta(days=30),
        status=KPIPeriodStatus.OPEN,
    )


def _local(year, month, day=1):
    return datetime(year, month, day, tzinfo=APPLICATION_TIMEZONE)


@pytest.fixture()
def seeded():
    engine = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    admin = SimpleNamespace(role=Role(name="Super Admin"))

    async def _seed(db):
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: KPIPeriod.__table__.create(
                    sync_conn, checkfirst=True
                )
            )
        db.add_all(
            [
                _period("Dec 2025", _local(2025, 12)),
                _period("Jan 2026", _local(2026, 1)),
                _period("Jun 2026", _local(2026, 6)),
                _period("Dec 2026", _local(2026, 12)),
                _period("Jan 2027", _local(2027, 1)),
            ]
        )
        await db.commit()

    return factory, admin, _seed


async def _labels(factory, admin, seed, **kwargs):
    async with factory() as db:
        await seed(db)
        rows = await get_kpi_periods(db=db, current_user=admin, **kwargs)
        return [row.label for row in rows]


@pytest.mark.asyncio
async def test_year_filter_returns_only_that_year(seeded):
    factory, admin, seed = seeded
    labels = await _labels(factory, admin, seed, skip=0, limit=50, year=2026)
    # Newest first, and ONLY 2026 — a January period (local midnight,
    # still December in UTC) must count as 2026.
    assert labels == ["Dec 2026", "Jun 2026", "Jan 2026"]


@pytest.mark.asyncio
async def test_no_year_keeps_existing_behaviour(seeded):
    factory, admin, seed = seeded
    labels = await _labels(factory, admin, seed, skip=0, limit=50, year=None)
    assert labels == ["Jan 2027", "Dec 2026", "Jun 2026", "Jan 2026", "Dec 2025"]


@pytest.mark.asyncio
async def test_year_combines_with_skip_and_limit(seeded):
    factory, admin, seed = seeded
    labels = await _labels(factory, admin, seed, skip=1, limit=1, year=2026)
    assert labels == ["Jun 2026"]
    empty = await _labels(factory, admin, seed, skip=0, limit=50, year=2024)
    assert empty == []
