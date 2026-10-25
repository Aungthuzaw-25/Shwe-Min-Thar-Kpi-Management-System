import asyncio
from datetime import UTC, date
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.connection import Base
from app.modules.departments.models import Department, UserDepartment
from app.modules.kpi.engine import (
    KPICalculationContext,
    KPICalculationEngine,
    _find_fallback_formula,
)
from app.modules.kpi.models import (
    FormulaScope,
    FormulaStatus,
    KPIAssignment,
    KPIFormula,
    KPIFormulaPeriod,
    KPIHistory,
    KPIPeriod,
    KPIPeriodStatus,
    KPIRecord,
    KPIScoringRuleItem,
    KPIStatus,
    PeriodType,
)
from app.modules.kpi.period_ranges import calendar_period_range
from app.modules.tasks.models.task import Task
from app.modules.tasks.models.task_review import TaskReview
from app.modules.users.models import Position, Role, User


async def _with_isolated_database(database_path, test_case, *args):
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    tables = [
        Department.__table__,
        UserDepartment.__table__,
        Role.__table__,
        Position.__table__,
        User.__table__,
        KPIFormula.__table__,
        KPIFormulaPeriod.__table__,
        KPIPeriod.__table__,
        KPIAssignment.__table__,
        KPIRecord.__table__,
        KPIHistory.__table__,
        KPIScoringRuleItem.__table__,
        Task.__table__,
        TaskReview.__table__,
    ]
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: Base.metadata.create_all(
                    sync_connection,
                    tables=tables,
                )
            )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as db:
            await test_case(db, *args)
    finally:
        await engine.dispose()


def _run_isolated_test(tmp_path, test_case, *args):
    database_path = tmp_path / "explicit-kpi-context.sqlite"
    asyncio.run(_with_isolated_database(database_path, test_case, *args))


async def _test_fallback_formula_resolution_by_user_level(db):
    start, end = _canonical_range()
    department = Department(name=f"Resolution Department {uuid4()}")
    role = Role(name=f"Resolution Role {uuid4()}")
    db.add_all([department, role])
    await db.flush()
    positions = {
        level: Position(
            name=f"Resolution Position {level} {uuid4()}",
            department_id=department.id,
        )
        for level in ("L0", "L1", "L2")
    }
    db.add_all(positions.values())
    await db.flush()

    users = {}
    for level in ("L0", "L1", "L2"):
        user = User(
            email=f"{uuid4()}@example.test",
            hashed_password=uuid4().hex,
            first_name="Resolution",
            last_name=level,
            role_id=role.id,
            position_id=positions[level].id,
            position=positions[level],
            is_active=True,
            is_deleted=False,
            user_departments=[
                UserDepartment(
                    department_id=department.id,
                    user_level=level,
                    is_deleted=False,
                )
            ],
        )
        db.add(user)
        users[level] = user
    await db.flush()

    def formula(
        level,
        version,
        month="2026-11",
        *,
        position_level=None,
        active=True,
        deleted=False,
    ):
        formula_position = positions[position_level or level]
        value = KPIFormula(
            name=f"Resolution {level} v{version} {month} {uuid4()}",
            department_id=department.id,
            position_name=formula_position.name,
            position_id=formula_position.id,
            user_level=level,
            categories=[{
                "name": "Reliable Work",
                "weight": 1.0,
                "max_score": 5.0,
                "evaluation_type": "DEDUCTION",
            }],
            scope=FormulaScope.DEPARTMENT,
            is_active=active,
            status=FormulaStatus.ACTIVE,
            effective_month=month,
            version=version,
            is_deleted=deleted,
        )
        db.add(value)
        return value

    expected = {
        level: formula(level, 1)
        for level in ("L0", "L1", "L2")
    }
    for level in ("L0", "L1", "L2"):
        for other_level in set(("L0", "L1", "L2")) - {level}:
            formula(other_level, 999, position_level=level)
    formula("L0", 999, month="2026-10", position_level="L0")
    formula("L0", 1000, position_level="L0", active=False)
    formula("L0", 1001, position_level="L0", deleted=True)
    await db.flush()

    for level, user in users.items():
        selected = await _find_fallback_formula(db, user, "2026-11")
        assert selected.id == expected[level].id

    fallback_higher_versions = {
        level: formula(level, 2000, position_level=level)
        for level in ("L0", "L1", "L2")
    }
    period = KPIPeriod(
        label=f"Resolution period {uuid4()}",
        start_date=start,
        end_date=end,
        status=KPIPeriodStatus.OPEN,
        is_deleted=False,
    )
    db.add(period)
    await db.flush()
    for level, user in users.items():
        db.add(
            KPIAssignment(
                department_id=department.id,
                position_id=positions[level].id,
                user_id=user.id,
                kpi_formula_id=expected[level].id,
                kpi_period_id=period.id,
                assigned_at=start.replace(tzinfo=None),
                is_deleted=False,
            )
        )
    await db.flush()

    for level, user in users.items():
        selected, selected_period = (
            await KPICalculationEngine.get_applicable_formula_and_period(
                db, user, start
            )
        )
        assert selected.id == expected[level].id
        assert selected.id != fallback_higher_versions[level].id
        assert selected_period.id == period.id

    formula("L0", 2000, position_level="L0")
    await db.flush()
    with pytest.raises(ValueError, match="equally-prioritized"):
        await _find_fallback_formula(db, users["L0"], "2026-11")


def test_fallback_formula_resolution_targets_user_level_and_preserves_assignment_precedence(
    tmp_path,
):
    _run_isolated_test(
        tmp_path,
        _test_fallback_formula_resolution_by_user_level,
    )


def _canonical_range():
    period_range = calendar_period_range(PeriodType.MONTHLY, date(2026, 11, 12))
    return (
        period_range.start_inclusive.astimezone(UTC),
        period_range.end_exclusive.astimezone(UTC),
    )


async def _seed_overlapping_contexts(db):
    start, end = _canonical_range()
    department = Department(name=f"Context Department {uuid4()}")
    role = Role(name=f"Context Role {uuid4()}")
    db.add_all([department, role])
    await db.flush()

    position = Position(name=f"Context Position {uuid4()}", department_id=department.id)
    db.add(position)
    await db.flush()

    user_a = User(
        email=f"{uuid4()}@example.test",
        hashed_password=uuid4().hex,
        first_name="Context",
        last_name="A",
        role_id=role.id,
        position_id=position.id,
        is_active=True,
        is_deleted=False,
    )
    user_b = User(
        email=f"{uuid4()}@example.test",
        hashed_password=uuid4().hex,
        first_name="Context",
        last_name="B",
        role_id=role.id,
        position_id=position.id,
        is_active=True,
        is_deleted=False,
    )
    db.add_all([user_a, user_b])
    await db.flush()

    formula_a = KPIFormula(
        name="Explicit Formula A",
        department_id=department.id,
        position_name=position.name,
        position_id=position.id,
        user_level="L2",
        categories=[{
            "name": "Reliable Work",
            "weight": 1.0,
            "max_score": 5.0,
            "evaluation_type": "DEDUCTION",
        }],
        scope=FormulaScope.DEPARTMENT,
        is_active=True,
        status=FormulaStatus.ACTIVE,
        effective_month="2026-11",
        version=1,
        is_deleted=False,
    )
    formula_b = KPIFormula(
        name="Fallback Candidate B",
        department_id=department.id,
        position_name=position.name,
        position_id=position.id,
        user_level="L2",
        categories=[{
            "name": "Reliable Work",
            "weight": 1.0,
            "max_score": 1.0,
            "evaluation_type": "DEDUCTION",
        }],
        scope=FormulaScope.DEPARTMENT,
        is_active=True,
        status=FormulaStatus.ACTIVE,
        effective_month="2026-11",
        version=1,
        is_deleted=False,
    )
    period_a = KPIPeriod(
        label="Overlapping Context Period A",
        start_date=start,
        end_date=end,
        status=KPIPeriodStatus.OPEN,
        is_deleted=False,
    )
    period_b = KPIPeriod(
        label="Overlapping Context Period B",
        start_date=start,
        end_date=end,
        status=KPIPeriodStatus.OPEN,
        is_deleted=False,
    )
    db.add_all([formula_a, formula_b, period_a, period_b])
    await db.flush()

    record_a = KPIRecord(
        user_id=user_a.id,
        department_id=department.id,
        formula_id=formula_a.id,
        kpi_period_id=period_a.id,
        period_type=PeriodType.MONTHLY,
        period_start=start,
        period_end=end,
        raw_score=1.0,
        weighted_score=1.0,
        percentage=20.0,
        status_bucket=KPIStatus.POOR,
        is_deleted=False,
    )
    record_b = KPIRecord(
        user_id=user_b.id,
        department_id=department.id,
        formula_id=formula_b.id,
        kpi_period_id=period_b.id,
        period_type=PeriodType.MONTHLY,
        period_start=start,
        period_end=end,
        raw_score=2.25,
        weighted_score=2.25,
        percentage=45.0,
        status_bucket=KPIStatus.FAIR,
        is_deleted=False,
    )
    assignment_a = KPIAssignment(
        department_id=department.id,
        position_id=position.id,
        user_id=user_a.id,
        kpi_formula_id=formula_a.id,
        kpi_period_id=period_a.id,
        assigned_at=start.replace(tzinfo=None),
        is_deleted=False,
    )
    assignment_b = KPIAssignment(
        department_id=department.id,
        position_id=position.id,
        user_id=user_b.id,
        kpi_formula_id=formula_b.id,
        kpi_period_id=period_b.id,
        assigned_at=start.replace(tzinfo=None),
        is_deleted=False,
    )
    db.add_all([record_a, record_b, assignment_a, assignment_b])
    await db.commit()

    return {
        "user_a": user_a,
        "user_b": user_b,
        "formula_a": formula_a,
        "formula_b": formula_b,
        "period_a": period_a,
        "period_b": period_b,
        "record_a": record_a,
        "record_b": record_b,
        "assignment_a": assignment_a,
        "assignment_b": assignment_b,
    }


async def _test_explicit_context_bypasses_overlap_and_targets_one_record(db, monkeypatch):
    fixture = await _seed_overlapping_contexts(db)
    start, end = _canonical_range()
    explicit_context = KPICalculationContext(
        record_id=fixture["record_a"].id,
        assignment_id=fixture["assignment_a"].id,
        period_id=fixture["period_a"].id,
        formula_id=fixture["formula_a"].id,
        user_id=fixture["user_a"].id,
    )
    other_record_before = (
        fixture["record_b"].weighted_score,
        fixture["record_b"].percentage,
        fixture["record_b"].formula_id,
        fixture["record_b"].kpi_period_id,
    )

    with pytest.raises(ValueError, match="Multiple active KPI periods overlap"):
        await KPICalculationEngine.execute_kpi_calculation(
            db,
            fixture["user_a"],
            start,
            end,
            PeriodType.MONTHLY,
        )
    await db.refresh(fixture["record_a"])
    await db.refresh(fixture["record_b"])

    async def reject_global_lookup(*_args, **_kwargs):
        raise AssertionError("Explicit context must not call global period lookup.")

    monkeypatch.setattr(
        KPICalculationEngine,
        "get_applicable_formula_and_period",
        reject_global_lookup,
    )
    calculated_record = await KPICalculationEngine.execute_kpi_calculation(
        db,
        context=explicit_context,
    )
    await db.refresh(fixture["record_a"])
    await db.refresh(fixture["record_b"])

    assert calculated_record.id == explicit_context.record_id
    assert fixture["record_a"].kpi_period_id == fixture["period_a"].id
    assert fixture["record_a"].formula_id == fixture["formula_a"].id
    assert fixture["record_a"].weighted_score == 5.0
    assert fixture["record_a"].percentage == 100.0
    assert (
        fixture["record_b"].weighted_score,
        fixture["record_b"].percentage,
        fixture["record_b"].formula_id,
        fixture["record_b"].kpi_period_id,
    ) == other_record_before


async def _test_record_id_alone_resolves_second_overlapping_period(db):
    fixture = await _seed_overlapping_contexts(db)
    record_b_id = fixture["record_b"].id
    period_b_id = fixture["period_b"].id
    formula_b_id = fixture["formula_b"].id

    await KPICalculationEngine.execute_kpi_calculation(
        db,
        context=KPICalculationContext(record_id=record_b_id),
    )
    record_b = await db.get(KPIRecord, record_b_id)

    assert record_b.kpi_period_id == period_b_id
    assert record_b.formula_id == formula_b_id
    assert record_b.weighted_score == 1.0
    assert record_b.percentage == 100.0


async def _test_context_rejects_contradictory_identifiers(db, case):
    fixture = await _seed_overlapping_contexts(db)
    context_values = {
        "record_id": fixture["record_a"].id,
        "assignment_id": fixture["assignment_a"].id,
        "period_id": fixture["period_a"].id,
        "formula_id": fixture["formula_a"].id,
        "user_id": fixture["user_a"].id,
    }
    field, replacement, message = case
    context_values[field] = replacement(fixture)
    context = KPICalculationContext(**context_values)

    with pytest.raises(ValueError, match=message):
        await KPICalculationEngine.resolve_explicit_calculation_context(db, context)


async def _test_missing_explicit_record_never_falls_back(db, monkeypatch):
    async def reject_global_lookup(*_args, **_kwargs):
        raise AssertionError("Missing explicit records must not fall back to date lookup.")

    monkeypatch.setattr(
        KPICalculationEngine,
        "get_applicable_formula_and_period",
        reject_global_lookup,
    )
    context = KPICalculationContext(record_id=uuid4())
    with pytest.raises(ValueError, match="KPI record was not found"):
        await KPICalculationEngine.execute_kpi_calculation(
            db,
            context=context,
        )


def test_explicit_context_bypasses_overlap_and_targets_exact_record(tmp_path, monkeypatch):
    _run_isolated_test(
        tmp_path,
        _test_explicit_context_bypasses_overlap_and_targets_one_record,
        monkeypatch,
    )


def test_record_id_alone_resolves_second_overlapping_period(tmp_path):
    _run_isolated_test(tmp_path, _test_record_id_alone_resolves_second_overlapping_period)


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("user_id", lambda data: data["user_b"].id, "does not belong to the requested user"),
        ("period_id", lambda data: data["period_b"].id, "does not belong to the requested period"),
        ("formula_id", lambda data: data["formula_b"].id, "does not use the requested formula"),
        ("assignment_id", lambda data: data["assignment_b"].id, "assignment does not match"),
    ],
)
def test_explicit_context_rejects_contradictory_identifiers(tmp_path, field, replacement, message):
    _run_isolated_test(
        tmp_path,
        _test_context_rejects_contradictory_identifiers,
        (field, replacement, message),
    )


def test_missing_explicit_record_never_falls_back(tmp_path, monkeypatch):
    _run_isolated_test(tmp_path, _test_missing_explicit_record_never_falls_back, monkeypatch)
