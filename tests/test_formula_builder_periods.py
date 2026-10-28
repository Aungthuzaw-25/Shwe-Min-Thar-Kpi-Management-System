"""Formula Builder period resolution (Backend stage, design doc
Stage 1): month refs, auto-create, custom ranges, unlink-block,
create safety, and overlap warnings.

Fixture convention: periods are stored with Yangon-aware
boundaries from ``calendar_period_range`` (as production
timestamptz holds them). SQLite drops the offset on storage, so
every value here is Yangon wall-clock and window comparisons stay
exact; the UTC-instant trap (Yangon Oct 1 = UTC Sep 30 17:30) is
covered by the boundary test through the aware helper.
"""

import asyncio
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.modules.departments.models  # noqa: F401 (mapper registry)
import app.modules.users.models  # noqa: F401
from app.database.base import BaseModel
from app.modules.departments.models import Department
from app.modules.kpi.formula_periods import (
    expand_month_range,
    find_overlap_warnings,
    find_period_for_month,
)
from app.modules.kpi.models import (
    FormulaScope,
    FormulaStatus,
    KPIFormula,
    KPIFormulaPeriod,
    KPIPeriod,
    KPIPeriodStatus,
    KPIRecord,
    PeriodType,
)
from app.modules.kpi.period_ranges import calendar_period_range
from app.modules.kpi.schemas import (
    KPIFormulaCreate,
    PeriodMonthRange,
    TIMELINESS_CATEGORY_NAME,
)
from app.modules.kpi.service import (
    create_kpi_formula,
    current_period_ids,
    set_formula_period_links,
)

CATEGORIES = [
    {"name": "Sales", "weight": 0.99, "evaluation_type": "TARGET"},
    {"name": TIMELINESS_CATEGORY_NAME, "weight": 0.01,
     "evaluation_type": "DEDUCTION"},
]


@pytest.fixture()
def world(tmp_path):
    db_file = tmp_path / "builder.db"
    sync_engine = create_engine(f"sqlite:///{db_file}")
    BaseModel.metadata.create_all(sync_engine)
    session = sessionmaker(sync_engine)()
    dept = Department(name="Sales")
    session.add(dept)
    # KpiCategory.id defaults to PG-only gen_random_uuid(), so the
    # sync step inside formula creation needs pre-seeded rows with
    # explicit ids on SQLite (standing test-env rule).
    from app.modules.kpi.models import KpiCategory

    session.add_all([
        KpiCategory(id=uuid4(), name="Sales"),
        KpiCategory(id=uuid4(), name=TIMELINESS_CATEGORY_NAME),
    ])
    session.commit()
    async_engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    factory = async_sessionmaker(async_engine, expire_on_commit=False)
    return SimpleNamespace(session=session, factory=factory, dept_id=dept.id)



def _period(label, year, month, end_year=None, end_month=None):
    rng = calendar_period_range(PeriodType.MONTHLY, date(year, month, 1))
    start, end = rng.start_inclusive, rng.end_exclusive
    if end_year is not None:
        end_rng = calendar_period_range(
            PeriodType.MONTHLY, date(end_year, end_month, 1)
        )
        end = end_rng.start_inclusive
    return KPIPeriod(
        label=label, start_date=start, end_date=end,
        status=KPIPeriodStatus.OPEN, is_deleted=False,
    )


def _create_payload(dept_id, **overrides):
    values = dict(
        name="Builder formula", department_id=dept_id,
        position_name="Officer", user_level="L2", categories=CATEGORIES,
    )
    values.update(overrides)
    return KPIFormulaCreate(**values)


def _run(coro):
    return asyncio.run(coro)


async def _formula_by_name(factory, name):
    async with factory() as db:
        return (await db.execute(
            select(KPIFormula).where(KPIFormula.name == name)
        )).scalars().first()


async def _all_periods(factory):
    async with factory() as db:
        return (await db.execute(select(KPIPeriod))).scalars().all()


# --- month refs ------------------------------------------------------

def test_month_ref_reuses_existing_period(world):
    async def scenario():
        async with world.factory() as db:
            october = _period("October 2026", 2026, 10)
            db.add(october)
            await db.commit()
            october_id = october.id
        created = await _create_via_service(
            world, _create_payload(
                world.dept_id, period_ids=["2026-10"]
            ), can_create_periods=False,
        )
        async with world.factory() as db:
            assert await current_period_ids(db, created.id) == [october_id]
            assert len(await _all_periods(world.factory)) == 1
    _run(scenario())


def test_month_ref_autocreates_missing_month(world):
    async def scenario():
        created = await _create_via_service(
            world, _create_payload(
                world.dept_id, period_ids=["2026-10"]
            ), can_create_periods=True,
        )
        async with world.factory() as db:
            links = await current_period_ids(db, created.id)
            assert len(links) == 1
            period = await db.get(KPIPeriod, links[0])
            assert period.label == "October 2026"
            assert period.status == KPIPeriodStatus.DRAFT
            rng = calendar_period_range(PeriodType.MONTHLY, date(2026, 10, 1))
            # SQLite drops tzinfo on storage; wall-clock equality is
            # the meaningful check (production timestamptz keeps it).
            assert period.start_date == rng.start_inclusive.replace(tzinfo=None)
            assert period.end_date == rng.end_exclusive.replace(tzinfo=None)
            formula = await db.get(KPIFormula, created.id)
            assert formula.effective_month is None
    _run(scenario())


def test_month_ref_missing_without_permission_rejected(world):
    async def scenario():
        async with world.factory() as db:
            with pytest.raises(HTTPException) as error:
                await create_kpi_formula(
                    db, _create_payload(
                        world.dept_id, period_ids=["2026-10"]
                    ), can_create_periods=False,
                )
            assert error.value.status_code == 422
            assert "October 2026" in str(error.value.detail)
            await db.rollback()
        assert await _formula_by_name(world.factory, "Builder formula") is None
        assert await _all_periods(world.factory) == []
    _run(scenario())


def test_exact_month_wins_over_quarterly(world):
    async def scenario():
        async with world.factory() as db:
            q4 = _period("Q4 2026", 2026, 10, end_year=2027, end_month=1)
            monthly = _period("October 2026", 2026, 10)
            db.add_all([q4, monthly])
            await db.commit()
            monthly_id = monthly.id
        created = await _create_via_service(
            world, _create_payload(
                world.dept_id, period_ids=["2026-10"]
            ), can_create_periods=False,
        )
        async with world.factory() as db:
            assert await current_period_ids(db, created.id) == [monthly_id]
    _run(scenario())


def test_yangon_month_boundary_not_utc_month(world):
    async def scenario():
        async with world.factory() as db:
            # Yangon Oct 1 00:00 is UTC Sep 30 17:30 — a UTC-month
            # lookup would file this period under September.
            october = _period("October 2026", 2026, 10)
            db.add(october)
            await db.commit()
            found = await find_period_for_month(db, 2026, 10)
            assert found is not None and found.label == "October 2026"
            assert await find_period_for_month(db, 2026, 9) is None
    _run(scenario())


# --- custom range ----------------------------------------------------

def test_custom_range_expands_inclusive(world):
    async def scenario():
        created = await _create_via_service(
            world, _create_payload(
                world.dept_id,
                period_month_range=PeriodMonthRange(
                    start_month="2026-10", end_month="2026-12"
                ),
            ), can_create_periods=True,
        )
        async with world.factory() as db:
            links = await current_period_ids(db, created.id)
            assert len(links) == 3
            periods = (await db.execute(
                select(KPIPeriod).where(KPIPeriod.id.in_(links))
            )).scalars().all()
            assert sorted(p.label for p in periods) == [
                "December 2026", "November 2026", "October 2026",
            ]
    _run(scenario())


def test_custom_range_validation():
    with pytest.raises(HTTPException) as error:
        expand_month_range("2026-12", "2026-10")
    assert error.value.status_code == 400
    with pytest.raises(HTTPException) as error:
        expand_month_range("2026-01", "2031-01")
    assert error.value.status_code == 422
    assert len(expand_month_range("2026-01", "2030-12")) == 60


# --- unlink-block ----------------------------------------------------

async def _create_via_service(world, payload, **kwargs):
    async with world.factory() as db:
        return await create_kpi_formula(db, payload, **kwargs)


async def _linked_formula(world, months=("2026-10",)):
    return await _create_via_service(
        world, _create_payload(world.dept_id, period_ids=list(months)),
        can_create_periods=True,
    )


def test_unlink_block_scored_period(world):
    async def scenario():
        created = await _linked_formula(world)
        async with world.factory() as db:
            october_id = (await current_period_ids(db, created.id))[0]
            period = await db.get(KPIPeriod, october_id)
            db.add(KPIRecord(
                formula_id=created.id, kpi_period_id=october_id,
                period_type=PeriodType.MONTHLY,
                period_start=period.start_date, period_end=period.end_date,
            ))
            await db.commit()
        async with world.factory() as db:
            formula = await db.get(KPIFormula, created.id)
            with pytest.raises(HTTPException) as error:
                await set_formula_period_links(db, formula, [])
            assert error.value.status_code == 422
            assert "October 2026" in str(error.value.detail)
            assert await current_period_ids(db, created.id) == [october_id]
    _run(scenario())


def test_unlink_block_legacy_record_window(world):
    async def scenario():
        created = await _linked_formula(world)
        async with world.factory() as db:
            october_id = (await current_period_ids(db, created.id))[0]
            period = await db.get(KPIPeriod, october_id)
            # Pre-link legacy record: no period id, start in window.
            db.add(KPIRecord(
                formula_id=created.id, kpi_period_id=None,
                period_type=PeriodType.MONTHLY,
                period_start=period.start_date, period_end=period.end_date,
            ))
            await db.commit()
            formula = await db.get(KPIFormula, created.id)
            with pytest.raises(HTTPException):
                await set_formula_period_links(db, formula, [])
    _run(scenario())


def test_unlink_unscored_period_keeps_history(world):
    async def scenario():
        created = await _linked_formula(world, months=("2026-10", "2026-11"))
        async with world.factory() as db:
            before = await current_period_ids(db, created.id)
            assert len(before) == 2
            november = (await db.execute(
                select(KPIPeriod).where(KPIPeriod.label == "November 2026")
            )).scalars().first()
            formula = await db.get(KPIFormula, created.id)
            await set_formula_period_links(db, formula, [november.id])
            await db.commit()
            rows = (await db.execute(
                select(KPIFormulaPeriod).where(
                    KPIFormulaPeriod.formula_id == created.id
                )
            )).scalars().all()
            assert len(rows) == 2  # nothing deleted
            assert sum(1 for row in rows if row.is_current) == 1
    _run(scenario())


def test_close_bypasses_unlink_block(world):
    async def scenario():
        created = await _linked_formula(world)
        async with world.factory() as db:
            october_id = (await current_period_ids(db, created.id))[0]
            period = await db.get(KPIPeriod, october_id)
            db.add(KPIRecord(
                formula_id=created.id, kpi_period_id=october_id,
                period_type=PeriodType.MONTHLY,
                period_start=period.start_date, period_end=period.end_date,
            ))
            await db.commit()
            formula = await db.get(KPIFormula, created.id)
            await set_formula_period_links(
                db, formula, [], allow_scored_unlink=True
            )
            assert await current_period_ids(db, created.id) == []
    _run(scenario())


# --- create safety ---------------------------------------------------

def test_create_rejects_neither_period_nor_month(world):
    async def scenario():
        async with world.factory() as db:
            with pytest.raises(HTTPException) as error:
                await create_kpi_formula(
                    db, _create_payload(world.dept_id),
                    can_create_periods=True,
                )
            assert error.value.status_code == 422
            await db.rollback()
        assert await _formula_by_name(world.factory, "Builder formula") is None
    _run(scenario())


def test_create_with_month_only_keeps_legacy_month(world):
    async def scenario():
        created = await _create_via_service(
            world, _create_payload(
                world.dept_id, effective_month="2026-10"
            ), can_create_periods=True,
        )
        async with world.factory() as db:
            formula = await db.get(KPIFormula, created.id)
            assert formula.effective_month == "2026-10"
            assert await current_period_ids(db, created.id) == []
    _run(scenario())


def test_create_with_periods_ignores_effective_month(world):
    async def scenario():
        created = await _create_via_service(
            world, _create_payload(
                world.dept_id, period_ids=["2026-10"],
                effective_month="2026-10",
            ), can_create_periods=True,
        )
        async with world.factory() as db:
            formula = await db.get(KPIFormula, created.id)
            assert formula.effective_month is None
    _run(scenario())


def test_failed_save_rolls_back_autocreated_period(world):
    async def scenario():
        async with world.factory() as db:
            with pytest.raises(HTTPException):
                await create_kpi_formula(
                    db, _create_payload(
                        world.dept_id, period_ids=["2026-10", "nonsense"]
                    ), can_create_periods=True,
                )
            await db.rollback()
        assert await _all_periods(world.factory) == []
        assert await _formula_by_name(world.factory, "Builder formula") is None
    _run(scenario())


# --- overlap warnings ------------------------------------------------

def _active_formula(dept_id, name, position_name="Officer"):
    return KPIFormula(
        name=name, department_id=dept_id, position_name=position_name,
        user_level="L2", categories=[], scope=FormulaScope.DEPARTMENT,
        is_active=True, status=FormulaStatus.ACTIVE, version=1,
        is_deleted=False,
    )


def test_overlap_warning_same_audience(world):
    async def scenario():
        async with world.factory() as db:
            october = _period("October 2026", 2026, 10)
            other = _active_formula(world.dept_id, "Other formula")
            db.add_all([october, other])
            await db.flush()
            db.add(KPIFormulaPeriod(
                formula_id=other.id, period_id=october.id, is_current=True
            ))
            saved = _active_formula(world.dept_id, "Saved formula")
            db.add(saved)
            await db.flush()
            warnings = await find_overlap_warnings(db, saved, [october.id])
            assert len(warnings) == 1
            warning = warnings[0]
            assert warning["period_label"] == "October 2026"
            assert warning["conflicting_formula_name"] == "Other formula"
            assert warning["shared_scope"]["user_level"] == "L2"
    _run(scenario())


def test_overlap_warning_skips_different_department(world):
    async def scenario():
        async with world.factory() as db:
            october = _period("October 2026", 2026, 10)
            db.add(october)
            await db.flush()
            other = _active_formula(world.dept_id, "Other formula")
            db.add(other)
            await db.flush()
            db.add(KPIFormulaPeriod(
                formula_id=other.id, period_id=october.id, is_current=True
            ))
            saved = _active_formula(world.dept_id, "Saved formula")
            # Different department: the engine never matches both.
            saved.department_id = uuid4()
            db.add(saved)
            await db.flush()
            assert await find_overlap_warnings(db, saved, [october.id]) == []
    _run(scenario())


# --- version-builder gap fix -----------------------------------------

from app.modules.kpi.engine import formula_applies_to_period  # noqa: E402
from app.modules.kpi.schemas import KPIFormulaUpdate  # noqa: E402
from app.modules.kpi.service import update_kpi_formula  # noqa: E402


async def _activate(world, formula_id):
    async with world.factory() as db:
        formula = await db.get(KPIFormula, formula_id)
        formula.status = FormulaStatus.ACTIVE
        await db.commit()


async def _update_via_service(world, formula_id, payload, **kwargs):
    async with world.factory() as db:
        return await update_kpi_formula(db, formula_id, payload, **kwargs)


def test_version_update_without_period_fields_inherits_links(world):
    async def scenario():
        created = await _linked_formula(world)
        await _activate(world, created.id)
        updated = await _update_via_service(
            world, created.id, KPIFormulaUpdate(name="Renamed")
        )
        assert updated.version == 2
        assert updated.status == FormulaStatus.DRAFT
        assert updated.name == "Renamed"
        async with world.factory() as db:
            old_links = await current_period_ids(db, created.id)
            new_links = await current_period_ids(db, updated.id)
            assert new_links == old_links
            assert len(new_links) == 1
            assert updated.effective_month is None
    _run(scenario())


def test_version_update_with_empty_periods_still_422(world):
    async def scenario():
        created = await _linked_formula(world)
        await _activate(world, created.id)
        async with world.factory() as db:
            with pytest.raises(HTTPException) as error:
                await update_kpi_formula(
                    db, created.id, KPIFormulaUpdate(period_ids=[])
                )
            assert error.value.status_code == 422
            await db.rollback()
    _run(scenario())


def test_version_update_without_period_fields_inherits_month(world):
    async def scenario():
        created = await _create_via_service(
            world, _create_payload(
                world.dept_id, effective_month="2026-10"
            ), can_create_periods=True,
        )
        await _activate(world, created.id)
        updated = await _update_via_service(
            world, created.id, KPIFormulaUpdate(name="Renamed")
        )
        assert updated.effective_month == "2026-10"
        async with world.factory() as db:
            assert await current_period_ids(db, updated.id) == []
    _run(scenario())


def test_version_update_neither_links_nor_month_422(world):
    async def scenario():
        async with world.factory() as db:
            broken = KPIFormula(
                name="Broken", department_id=world.dept_id,
                position_name="Officer", user_level="L2", categories=[],
                scope=FormulaScope.DEPARTMENT, is_active=True,
                status=FormulaStatus.ACTIVE, version=1, is_deleted=False,
                effective_month=None,
            )
            db.add(broken)
            await db.commit()
            broken_id = broken.id
        async with world.factory() as db:
            with pytest.raises(HTTPException) as error:
                await update_kpi_formula(
                    db, broken_id, KPIFormulaUpdate(name="Renamed")
                )
            assert error.value.status_code == 422
            await db.rollback()
    _run(scenario())


def test_activated_inherited_draft_stays_scoped(world):
    async def scenario():
        created = await _linked_formula(world)
        await _activate(world, created.id)
        updated = await _update_via_service(
            world, created.id, KPIFormulaUpdate(name="Renamed")
        )
        async with world.factory() as db:
            november = _period("November 2026", 2026, 11)
            db.add(november)
            await db.commit()
            new_version = await db.get(KPIFormula, updated.id)
            new_version.status = FormulaStatus.ACTIVE
            await db.commit()
            october_id = (await current_period_ids(db, updated.id))[0]
            october = await db.get(KPIPeriod, october_id)
            assert await formula_applies_to_period(db, new_version, october) is True
            assert await formula_applies_to_period(db, new_version, november) is False
    _run(scenario())
