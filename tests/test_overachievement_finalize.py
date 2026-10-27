"""Finalize wiring for over-achievement notices (QA FAIL fix pass):
commit=False paths defer until the outer commit, frozen records
notify nothing, and a notice failure never breaks the calculation."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.future import select

from app.database.connection import Base
from app.models.audit_log import AuditLog
from app.modules.departments.models import Department, UserDepartment
from app.modules.kpi import engine as engine_module
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
from app.modules.kpi.overachievement import flush_pending_notifications
from app.modules.notifications.models import Notification
from app.modules.targets.models import MetricType, Target, TargetStatus
from app.modules.tasks.models.task import Task, TaskStatus
from app.modules.tasks.models.task_review import TaskReview
from app.modules.users.models import Position, Role, User

TABLES = [
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
    Target.__table__,
    Notification.__table__,
]


async def _run_scenario(database_path, monkeypatch, case):
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: Base.metadata.create_all(
                    sync_connection, tables=TABLES
                )
            )
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as db:
            department = Department(name=f"Finalize Department {uuid4()}")
            role = Role(name=f"Finalize Role {uuid4()}")
            sa_role = Role(name="Super Admin")
            admin_role = Role(name="Admin")
            db.add_all([department, role, sa_role, admin_role])
            await db.flush()
            position = Position(
                name=f"Finalize Position {uuid4()}",
                department_id=department.id,
            )
            user = User(
                email=f"{uuid4()}@example.test",
                hashed_password="x",
                first_name="Finalize",
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
            for key, role_row in (("sa", sa_role), ("admin", admin_role)):
                db.add(User(
                    email=f"{key}-{uuid4()}@example.test",
                    hashed_password="x",
                    first_name=key, last_name="Test",
                    role_id=role_row.id,
                    permission_overrides=[],
                    is_active=True, is_deleted=False,
                ))
            period_start = datetime(2026, 7, 1, tzinfo=UTC)
            period_end = datetime(2026, 8, 1, tzinfo=UTC)
            period = KPIPeriod(
                label=f"Finalize period {uuid4()}",
                start_date=period_start,
                end_date=period_end,
                status=KPIPeriodStatus.OPEN,
                is_deleted=False,
            )
            formula = KPIFormula(
                name=f"Finalize Formula {uuid4()}",
                department_id=department.id,
                position_name=position.name,
                position_id=position.id,
                user_level="L2",
                categories=[{
                    "name": "Sales",
                    "weight": 1.0,
                    "max_score": 5.0,
                    "evaluation_type": "TARGET",
                }],
                scope=FormulaScope.DEPARTMENT,
                is_active=True,
                status=FormulaStatus.ACTIVE,
                effective_month="2026-07",
                version=1,
                is_deleted=False,
            )
            db.add_all([period, formula])
            await db.flush()
            await assign_kpi_to_user(
                db,
                {
                    "department_id": department.id,
                    "position_id": position.id,
                    "user_id": user.id,
                    "kpi_formula_id": formula.id,
                    "kpi_period_id": period.id,
                },
                user.id,
            )
            db.add(Target(
                name="Sales",
                department_id=department.id,
                target_value=100.0,
                metric_type=MetricType.PERCENTAGE,
                target_month="2026-07",
                status=TargetStatus.ACTIVE,
                is_deleted=False,
            ))
            await db.commit()

            async def calculate(**kwargs):
                await KPICalculationEngine.execute_kpi_calculation(
                    db, user, period_start, period_end,
                    PeriodType.MONTHLY, task_data={"sales": 125.0},
                    **kwargs,
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

            await case(db, user, calculate, monkeypatch)
    finally:
        await engine.dispose()


def _recorded_notifier(monkeypatch):
    calls = []
    recorder = AsyncMock(side_effect=lambda *a, **k: calls.append(k))
    monkeypatch.setattr(
        engine_module, "notify_overachievements", recorder
    )
    return calls


def test_finalize_defers_notice_until_flush(tmp_path, monkeypatch):
    async def case(db, user, calculate, monkeypatch):
        calls = _recorded_notifier(monkeypatch)
        pending: list = []
        record = await calculate(commit=False, pending_notifications=pending)
        # Nothing sent during the uncommitted calculation...
        assert calls == []
        # ...but the exceedance is collected and stored on the record.
        assert len(pending) == 1
        assert pending[0]["employee_id"] == user.id
        assert record.exceeded_categories[0]["exceeded"] is True
        assert record.exceeded_categories[0]["exceeded_pct"] == 25.0
        await db.commit()
        # ...and only the post-commit flush sends it (2 recipients).
        sent_calls = []

        async def fake_notifier(**kwargs):
            sent_calls.append(kwargs)

        sent = await flush_pending_notifications(
            db, pending, notifier=fake_notifier
        )
        assert len(sent_calls) == 2
        assert len(sent) == 2

    asyncio.run(_run_scenario(
        tmp_path / "over-finalize-defer.sqlite", monkeypatch, case
    ))


def test_finalize_frozen_record_sends_no_notice(tmp_path, monkeypatch):
    async def case(db, user, calculate, monkeypatch):
        calls = _recorded_notifier(monkeypatch)
        record = await calculate()
        assert record.weighted_score == pytest.approx(5.0)
        assert len(calls) == 1
        calls.clear()
        record.is_frozen = True
        await db.commit()
        frozen_score = record.weighted_score
        await calculate()
        assert calls == []
        assert record.weighted_score == frozen_score

    asyncio.run(_run_scenario(
        tmp_path / "over-finalize-frozen.sqlite", monkeypatch, case
    ))


def test_finalize_notice_failure_does_not_break_calculation(tmp_path, monkeypatch):
    async def case(db, user, calculate, monkeypatch):
        monkeypatch.setattr(
            engine_module, "notify_overachievements",
            AsyncMock(side_effect=RuntimeError("notify down")),
        )
        record = await calculate()
        assert record.weighted_score == pytest.approx(5.0)
        assert record.exceeded_categories[0]["exceeded"] is True

    asyncio.run(_run_scenario(
        tmp_path / "over-finalize-failsafe.sqlite", monkeypatch, case
    ))
