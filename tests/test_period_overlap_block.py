"""Period overlap block on create (Database design Stage 1).

Design: ~/workspace/your_files/agent-docs/database/
period-overlap-block-design.md

All dates are Yangon midnights unless a case says UTC input.
Existing October = [Oct 1, Nov 1) Yangon, status OPEN, unless
a case re-seeds it.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.timezone import APPLICATION_TIMEZONE
from app.modules.kpi.engine import KPICalculationEngine
from app.modules.kpi.formula_periods import find_or_create_period_for_month
from app.modules.kpi.models import (
    KPIAssignment,
    KPIFormula,
    KPIPeriod,
    KPIPeriodStatus,
)
from app.modules.kpi.period_router import create_kpi_period
from app.modules.kpi.period_schemas import KPIPeriodCreate
from app.modules.users.models import Role, User

ADMIN = SimpleNamespace(role=Role(name="Super Admin"))


def _local(year, month, day=1):
    return datetime(year, month, day, tzinfo=APPLICATION_TIMEZONE)


def _run(coro_factory, tmp_path):
    db_file = tmp_path / "overlap.db"
    sync_engine = create_engine(f"sqlite:///{db_file}")
    from app.database.base import BaseModel

    BaseModel.metadata.create_all(sync_engine)
    sync_engine.dispose()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}", poolclass=None
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _main():
        async with factory() as db:
            return await coro_factory(db)

    try:
        return asyncio.run(_main())
    finally:
        asyncio.run(engine.dispose())


async def _seed_october(db, **overrides):
    values = {
        "label": "October 2026",
        "start_date": _local(2026, 10),
        "end_date": _local(2026, 11),
        "status": KPIPeriodStatus.OPEN,
        "is_deleted": False,
    }
    values.update(overrides)
    period = KPIPeriod(**values)
    db.add(period)
    await db.commit()
    return period


async def _create(db, label, start, end):
    return await create_kpi_period(
        payload=KPIPeriodCreate(
            label=label, start_date=start, end_date=end
        ),
        db=db,
        current_user=ADMIN,
    )


async def _create_exc(db, label, start, end):
    try:
        await _create(db, label, start, end)
        return None
    except HTTPException as exc:
        return exc


async def _count(db):
    rows = (await db.execute(select(KPIPeriod))).scalars().all()
    return len(rows)


def test_exact_overlap_blocked_and_creates_nothing(tmp_path):
    async def _case(db):
        await _seed_october(db)
        exc = await _create_exc(
            db, "October copy", _local(2026, 10), _local(2026, 11)
        )
        assert exc is not None and exc.status_code == 422
        assert "October 2026" in exc.detail
        assert "1 Oct 2026 – 31 Oct 2026" in exc.detail
        assert await _count(db) == 1

    _run(_case, tmp_path)


@pytest.mark.parametrize(
    "label,start,end",
    [
        ("Partial start inside", _local(2026, 10, 15), _local(2026, 11, 16)),
        ("Partial end inside", _local(2026, 9, 15), _local(2026, 10, 16)),
        ("Contained", _local(2026, 10, 10), _local(2026, 10, 21)),
        ("Containing", _local(2026, 9, 1), _local(2027, 1, 1)),
    ],
)
def test_partial_contained_containing_blocked(tmp_path, label, start, end):
    async def _case(db):
        await _seed_october(db)
        exc = await _create_exc(db, label, start, end)
        assert exc is not None and exc.status_code == 422
        assert "October 2026" in exc.detail
        assert await _count(db) == 1

    _run(_case, tmp_path)


@pytest.mark.parametrize(
    "label,start,end",
    [
        ("Adjacent before", _local(2026, 9, 1), _local(2026, 10, 1)),
        ("Adjacent after", _local(2026, 11, 1), _local(2026, 12, 1)),
        ("Far apart", _local(2026, 3, 1), _local(2026, 4, 1)),
    ],
)
def test_adjacent_and_far_apart_allowed(tmp_path, label, start, end):
    async def _case(db):
        await _seed_october(db)
        period = await _create(db, label, start, end)
        assert period.label == label
        assert await _count(db) == 2

    _run(_case, tmp_path)


def test_deleted_and_cancelled_ignored(tmp_path):
    async def _case(db):
        await _seed_october(db, is_deleted=True)
        await _seed_october(
            db, label="October cancelled", status=KPIPeriodStatus.CANCELLED
        )
        period = await _create(
            db, "October fresh", _local(2026, 10), _local(2026, 11)
        )
        assert period.label == "October fresh"

    _run(_case, tmp_path)


@pytest.mark.parametrize(
    "status",
    [
        KPIPeriodStatus.DRAFT,
        KPIPeriodStatus.FINALIZED,
        KPIPeriodStatus.LOCKED,
    ],
)
def test_draft_finalized_locked_block(tmp_path, status):
    async def _case(db):
        await _seed_october(db, status=status)
        exc = await _create_exc(
            db, "October copy", _local(2026, 10), _local(2026, 11)
        )
        assert exc is not None and exc.status_code == 422

    _run(_case, tmp_path)


def test_malformed_dates_still_400_checked_first(tmp_path):
    async def _case(db):
        await _seed_october(db)
        exc = await _create_exc(
            db, "Backwards", _local(2026, 11, 1), _local(2026, 10, 1)
        )
        assert exc is not None and exc.status_code == 400
        assert await _count(db) == 1

    _run(_case, tmp_path)


def test_timezone_boundary_utc_input_normalizes_to_yangon(tmp_path):
    async def _case(db):
        await _seed_october(db)
        # Oct 31 17:30 UTC == Nov 1 00:00 Yangon: adjacent, allowed.
        period = await _create(
            db,
            "November 2026",
            datetime(2026, 10, 31, 17, 30, tzinfo=UTC),
            datetime(2026, 11, 30, 17, 30, tzinfo=UTC),
        )
        assert period.start_date.month == 11
        assert await _count(db) == 2

    _run(_case, tmp_path)


def test_multiple_overlaps_all_named(tmp_path):
    async def _case(db):
        await _seed_october(db)
        q4 = KPIPeriod(
            label="Q4 2026",
            start_date=_local(2026, 10),
            end_date=_local(2027, 1),
            status=KPIPeriodStatus.OPEN,
            is_deleted=False,
        )
        db.add(q4)
        await db.commit()
        exc = await _create_exc(
            db, "Covering", _local(2026, 9, 1), _local(2027, 2, 1)
        )
        assert exc is not None and exc.status_code == 422
        assert "October 2026" in exc.detail
        assert "Q4 2026" in exc.detail
        assert "1 Oct 2026 – 31 Dec 2026" in exc.detail
        assert await _count(db) == 2

    _run(_case, tmp_path)


def test_engine_ignores_cancelled_period(tmp_path):
    async def _case(db):
        role = Role(name="Employee")
        db.add(role)
        await db.flush()
        user = User(
            email="engine@example.test",
            hashed_password="x",
            first_name="Engine",
            last_name="User",
            role_id=role.id,
            is_active=True,
            is_deleted=False,
        )
        db.add(user)
        await db.flush()
        live = await _seed_october(db)
        await _seed_october(
            db, label="October cancelled", status=KPIPeriodStatus.CANCELLED
        )
        from uuid import uuid4

        formula = KPIFormula(
            name="Engine formula",
            # FK enforcement is off in SQLite tests; the assignment
            # path does not filter on department.
            department_id=uuid4(),
            user_level="L2",
            categories=[],
            status="ACTIVE",
            is_active=True,
            effective_month=None,
        )
        db.add(formula)
        await db.flush()
        db.add(
            KPIAssignment(
                department_id=formula.department_id,
                position_id=uuid4(),
                user_id=user.id,
                kpi_formula_id=formula.id,
                kpi_period_id=live.id,
                assigned_at=_local(2026, 10),
                is_deleted=False,
            )
        )
        await db.commit()
        # Without the CANCELLED filter this raises "Multiple active
        # KPI periods overlap the calculation date".
        found_formula, found_period = (
            await KPICalculationEngine.get_applicable_formula_and_period(
                db, user, _local(2026, 10, 15)
            )
        )
        assert found_period is not None
        assert found_period.id == live.id
        assert found_formula is not None

    _run(_case, tmp_path)


def test_auto_create_reuses_covering_period(tmp_path):
    async def _case(db):
        spanning = KPIPeriod(
            label="Sep-Nov span",
            start_date=_local(2026, 9, 15),
            end_date=_local(2026, 11, 15),
            status=KPIPeriodStatus.OPEN,
            is_deleted=False,
        )
        db.add(spanning)
        await db.commit()
        found = await find_or_create_period_for_month(
            db, 2026, 10, allow_create=True
        )
        assert found is not None
        assert found.id == spanning.id
        assert await _count(db) == 1

    _run(_case, tmp_path)


def test_auto_create_prefers_start_month_then_creates(tmp_path):
    async def _case(db):
        exact = await _seed_october(db)
        spanning = KPIPeriod(
            label="Sep-Nov span",
            start_date=_local(2026, 9, 15),
            end_date=_local(2026, 11, 15),
            status=KPIPeriodStatus.OPEN,
            is_deleted=False,
        )
        db.add(spanning)
        await db.commit()
        found = await find_or_create_period_for_month(
            db, 2026, 10, allow_create=True
        )
        assert found.id == exact.id
        created = await find_or_create_period_for_month(
            db, 2026, 12, allow_create=True
        )
        assert created is not None
        assert created.label == "December 2026"
        assert created.status == KPIPeriodStatus.DRAFT
        assert await _count(db) == 3

    _run(_case, tmp_path)
