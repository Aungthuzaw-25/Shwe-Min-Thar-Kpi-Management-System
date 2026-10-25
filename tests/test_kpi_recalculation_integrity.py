import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.future import select

from app.core.timezone import as_application_time
from app.database.connection import Base
from app.models.audit_log import AuditLog
from app.modules.departments.models import Department, UserDepartment
from app.modules.kpi.assignment_service import assign_kpi_to_user
from app.modules.kpi.engine import KPICalculationEngine
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
    PeriodType,
)
from app.modules.kpi.period_service import change_period_status
from app.modules.tasks.models.task import Task, TaskStatus
from app.modules.tasks.models.task_review import TaskReview
from app.modules.users.models import Position, Role, User


async def _run_integrity_scenario(database_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    tables = [
        Department.__table__,
        UserDepartment.__table__,
        Role.__table__,
        Position.__table__,
        User.__table__,
        AuditLog.__table__,
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
            department = Department(name=f"Integrity Department {uuid4()}")
            role = Role(name=f"Integrity Role {uuid4()}")
            db.add_all([department, role])
            await db.flush()

            position = Position(
                name=f"Integrity Position {uuid4()}",
                department_id=department.id,
            )
            user = User(
                email=f"{uuid4()}@example.test",
                hashed_password=uuid4().hex,
                first_name="Integrity",
                last_name="Owner",
                role_id=role.id,
                position=position,
                is_active=True,
                is_deleted=False,
                user_departments=[
                    UserDepartment(
                        department_id=department.id,
                        user_level="L2",
                        is_deleted=False,
                    )
                ],
            )
            db.add_all([position, user])
            await db.flush()

            period_start = datetime(2026, 7, 1, tzinfo=UTC)
            period_end = datetime(2026, 8, 1, tzinfo=UTC)
            period = KPIPeriod(
                label=f"Integrity period {uuid4()}",
                start_date=period_start,
                end_date=period_end,
                status=KPIPeriodStatus.OPEN,
                is_deleted=False,
            )

            def make_formula(name, max_score):
                formula = KPIFormula(
                    name=name,
                    department_id=department.id,
                    position_name=position.name,
                    position_id=position.id,
                    user_level="L2",
                    categories=[{
                        "name": "Late Submission",
                        "weight": 1.0,
                        "max_score": max_score,
                        "evaluation_type": "DEDUCTION",
                    }],
                    scope=FormulaScope.DEPARTMENT,
                    is_active=True,
                    status=FormulaStatus.ACTIVE,
                    effective_month="2026-07",
                    version=1,
                    is_deleted=False,
                )
                formula.scoring_rules = [
                    KPIScoringRuleItem(
                        category_name="late submission",
                        rule_name="late submission",
                        measure_type="count",
                        operator=">=",
                        target_value=1.0,
                        deduct_score=0.5,
                        is_deleted=False,
                    )
                ]
                return formula

            formula_a = make_formula("Integrity Formula A", 5.0)
            formula_b = make_formula("Integrity Formula B", 3.0)
            db.add_all([period, formula_a, formula_b])
            await db.flush()
            formula_a_id = formula_a.id
            formula_b_id = formula_b.id
            period_id = period.id
            user_id = user.id

            assignment_a = await assign_kpi_to_user(
                db,
                {
                    "department_id": department.id,
                    "position_id": position.id,
                    "user_id": user.id,
                    "kpi_formula_id": formula_a.id,
                    "kpi_period_id": period.id,
                },
                user.id,
            )
            with pytest.raises(HTTPException) as duplicate:
                await assign_kpi_to_user(
                    db,
                    {
                        "department_id": department.id,
                        "position_id": position.id,
                        "user_id": user.id,
                        "kpi_formula_id": formula_a.id,
                        "kpi_period_id": period.id,
                    },
                    user.id,
                )
            assert duplicate.value.status_code == 400

            db.add(
                Task(
                    title="Late source task",
                    category="Late Submission",
                    owner_id=user.id,
                    due_date=period_start + timedelta(days=5),
                    is_late=True,
                    late_days=1,
                    status=TaskStatus.COMPLETED,
                    is_deleted=False,
                )
            )
            await db.commit()

            async def calculate():
                await KPICalculationEngine.execute_kpi_calculation(
                    db,
                    user,
                    period_start,
                    period_end,
                    PeriodType.MONTHLY,
                )
                return (
                    await db.execute(
                        select(KPIRecord).where(
                            KPIRecord.user_id == user.id,
                            KPIRecord.period_start == period_start,
                            KPIRecord.is_deleted.is_(False),
                        )
                    )
                ).scalars().one()

            record_1 = await calculate()
            first_id = record_1.id
            assert record_1.weighted_score == pytest.approx(4.5)
            assert record_1.formula_id == formula_a.id
            assert record_1.kpi_period_id == period.id
            assert record_1.department_id == department.id

            record_2 = await calculate()
            record_3 = await calculate()
            assert record_2.id == record_3.id == first_id
            assert record_2.weighted_score == record_3.weighted_score == pytest.approx(4.5)
            initial_history = (
                await db.execute(select(KPIHistory).where(KPIHistory.kpi_record_id == first_id))
            ).scalars().all()
            assert not initial_history, "; ".join(row.reason for row in initial_history)

            assignment_a.is_deleted = True
            await db.commit()
            assignment_b = await assign_kpi_to_user(
                db,
                {
                    "department_id": department.id,
                    "position_id": position.id,
                    "user_id": user.id,
                    "kpi_formula_id": formula_b.id,
                    "kpi_period_id": period.id,
                },
                user.id,
            )
            assignment_b_id = assignment_b.id
            record_4 = await calculate()
            assert record_4.id == first_id
            assert record_4.weighted_score == pytest.approx(2.5)
            assert record_4.formula_id == formula_b_id
            assert record_4.department_id == department.id
            assert record_4.kpi_period_id == period_id
            assert assignment_b.kpi_formula_id == record_4.formula_id

            history_rows = (
                await db.execute(
                    select(KPIHistory).where(
                        KPIHistory.kpi_record_id == first_id
                    )
                )
            ).scalars().all()
            assert len(history_rows) == 1
            assert str(formula_a_id) in history_rows[0].reason
            assert str(formula_b_id) in history_rows[0].reason
            await calculate()
            assert len(
                (
                    await db.execute(
                        select(KPIHistory.id).where(
                            KPIHistory.kpi_record_id == first_id
                        )
                    )
                ).scalars().all()
            ) == 1
            assert len(
                (
                    await db.execute(
                        select(KPIRecord.id).where(
                            KPIRecord.user_id == user.id,
                            KPIRecord.period_start == period_start,
                            KPIRecord.is_deleted.is_(False),
                        )
                    )
                ).scalars().all()
            ) == 1

            await change_period_status(
                db, period_id, KPIPeriodStatus.FINALIZED, user_id
            )
            finalized = await change_period_status(
                db, period_id, KPIPeriodStatus.FINALIZED, user_id
            )
            assert finalized.status == KPIPeriodStatus.FINALIZED
            with pytest.raises(ValueError, match="finalized"):
                await calculate()

            await db.refresh(record_4)
            assert record_4.id == first_id
            assert record_4.formula_id == formula_b_id
            assert record_4.kpi_period_id == period_id
            assert record_4.weighted_score == pytest.approx(2.5)
            assert len(
                (
                    await db.execute(
                        select(KPIHistory.id).where(
                            KPIHistory.kpi_record_id == first_id
                        )
                    )
                ).scalars().all()
            ) == 1
            assert await db.scalar(
                select(KPIAssignment.id).where(
                    KPIAssignment.user_id == user_id,
                    KPIAssignment.kpi_period_id == period_id,
                    KPIAssignment.is_deleted.is_(False),
                )
            ) == assignment_b_id
    finally:
        await engine.dispose()


def test_recalculation_reuses_result_and_preserves_resolved_formula_provenance(
    tmp_path,
):
    asyncio.run(_run_integrity_scenario(tmp_path / "kpi-integrity.sqlite"))


@pytest.mark.asyncio
async def test_scoring_uses_application_local_month_for_utc_calendar_boundary(
    monkeypatch,
):
    import app.modules.kpi.engine as engine_module

    period_start = datetime(2026, 6, 30, 17, 30, tzinfo=UTC)
    period_end = datetime(2026, 7, 31, 17, 30, tzinfo=UTC)
    formula = SimpleNamespace(
        id=uuid4(),
        department_id=uuid4(),
        categories=[{"name": "Base", "weight": 1.0, "max_score": 5.0}],
    )
    user = SimpleNamespace(id=uuid4())
    captured = {}

    async def resolve_formula_and_period(_db, _user, _target_date):
        return formula, None

    async def collect_deductions(_db, _user, _formula, _start, _end):
        return {}, {}

    async def capture_score(_db, _formula, _department_id, target_month, _data):
        captured["target_month"] = target_month
        return 5.0

    async def skip_persistence(_db, _user, _formula, _period, _period_type, _start, _end, _score, _percentage):
        captured["persisted"] = True

    monkeypatch.setattr(
        KPICalculationEngine,
        "get_applicable_formula_and_period",
        staticmethod(resolve_formula_and_period),
    )
    monkeypatch.setattr(engine_module, "_collect_deductions", collect_deductions)
    monkeypatch.setattr(engine_module, "score_formula_categories", capture_score)
    monkeypatch.setattr(engine_module, "_save_kpi_record", skip_persistence)

    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    await KPICalculationEngine.execute_kpi_calculation(
        db,
        user,
        period_start,
        period_end,
        PeriodType.MONTHLY,
    )

    assert as_application_time(period_start).strftime("%Y-%m") == "2026-07"
    assert captured == {"target_month": "2026-07", "persisted": True}
