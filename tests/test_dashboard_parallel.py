"""Dashboard parallelization: payload shape + concurrency mechanism.

These tests pin BEHAVIOUR, not implementation: they pass on the old
sequential builders and must pass unchanged on the parallel builders.
That is the point — parallelization changes execution order, never
results. A second test proves the gather helper actually overlaps work
instead of running it one after another.
"""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.connection import Base
from app.modules.departments.models import Department, UserDepartment
from app.modules.kpi.dashboard_service import build_dashboard_summary
from app.modules.kpi.executive_intelligence_service import (
    build_executive_overview,
)
from app.modules.kpi.models import (
    KPIStatus,
    KPIPeriod,
    KPIPeriodStatus,
    KPIRecord,
    PeriodType,
)
from app.modules.kpi.parallel import gather_on_sessions
from app.modules.notifications.models import Notification
from app.modules.tasks.models.activity_timeline import ActivityTimeline
from app.modules.tasks.models.task import Task, TaskStatus
from app.modules.users.models import Role, User


async def _world(database_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            role = Role(name="Super Admin")
            dept = Department(name=f"Parallel dept {uuid4()}")
            db.add_all([role, dept])
            await db.flush()
            sa = User(
                email=f"{uuid4()}@example.test",
                hashed_password=uuid4().hex,
                first_name="Super",
                last_name="Admin",
                role_id=role.id,
                role=role,
                permission_overrides=[],
                is_active=True,
                is_deleted=False,
            )
            emp = User(
                email=f"{uuid4()}@example.test",
                hashed_password=uuid4().hex,
                first_name="Emp",
                last_name="Test",
                role_id=role.id,
                role=role,
                permission_overrides=[],
                is_active=True,
                is_deleted=False,
            )
            db.add_all([sa, emp])
            await db.flush()
            db.add_all(
                [
                    UserDepartment(
                        user_id=sa.id,
                        department_id=dept.id,
                        user_level="L2",
                        is_deleted=False,
                    ),
                    UserDepartment(
                        user_id=emp.id,
                        department_id=dept.id,
                        user_level="L2",
                        is_deleted=False,
                    ),
                ]
            )
            period = KPIPeriod(
                label="October 2026",
                start_date=datetime(2026, 10, 1, tzinfo=UTC),
                end_date=datetime(2026, 10, 31, 23, 59, tzinfo=UTC),
                status=KPIPeriodStatus.FINALIZED,
                is_deleted=False,
            )
            db.add(period)
            await db.flush()
            db.add_all(
                [
                    KPIRecord(
                        user_id=emp.id,
                        department_id=dept.id,
                        kpi_period_id=period.id,
                        period_type=PeriodType.MONTHLY,
                        period_start=period.start_date,
                        period_end=period.end_date,
                        raw_score=4.0,
                        weighted_score=4.0,
                        percentage=80.0,
                        status_bucket=KPIStatus.VERY_GOOD,
                        is_deleted=False,
                    ),
                    KPIRecord(
                        user_id=sa.id,
                        department_id=dept.id,
                        kpi_period_id=period.id,
                        period_type=PeriodType.MONTHLY,
                        period_start=period.start_date,
                        period_end=period.end_date,
                        raw_score=3.0,
                        weighted_score=3.0,
                        percentage=60.0,
                        status_bucket=KPIStatus.GOOD,
                        is_deleted=False,
                    ),
                    Task(
                        title="Done task",
                        owner_id=emp.id,
                        status=TaskStatus.COMPLETED,
                        is_deleted=False,
                    ),
                    Task(
                        title="Pending task",
                        owner_id=emp.id,
                        status=TaskStatus.PENDING_L1,
                        is_deleted=False,
                    ),
                    Notification(
                        user_id=sa.id,
                        title="Unread note",
                        message="hello",
                        notification_type="INFO",
                        is_read=False,
                        is_deleted=False,
                    ),
                    Notification(
                        user_id=sa.id,
                        title="Read note",
                        message="hello",
                        notification_type="INFO",
                        is_read=True,
                        is_deleted=False,
                    ),
                    ActivityTimeline(
                        user_id=emp.id,
                        action="TASK_COMPLETED",
                        entity_type="Task",
                        entity_id=uuid4(),
                        is_deleted=False,
                    ),
                ]
            )
            await db.commit()
            yield db, sa, emp, dept, period
    finally:
        await engine.dispose()


def _run(coro):
    return asyncio.run(coro)


def test_summary_payload_shape_and_values(tmp_path):
    async def case():
        async for db, sa, emp, dept, period in _world(
            tmp_path / "summary.sqlite"
        ):
            summary = await build_dashboard_summary(
                db,
                sa,
                scope_user_ids=[sa.id, emp.id],
                department_ids=[dept.id],
                period_type=PeriodType.MONTHLY,
                period_start=datetime(2026, 10, 1, tzinfo=UTC),
                period_end=datetime(2026, 11, 1, tzinfo=UTC),
            )
            assert summary.scope.role == sa.role.name
            assert summary.scope.user_count == 2
            assert summary.scorecards.evaluated_kpi_records == 2
            assert summary.scorecards.employee_count == 2
            assert summary.scorecards.department_count == 1
            # (80 + 60) / 2 = 70, (4.0 + 3.0) / 2 = 3.5
            assert summary.scorecards.kpi_percentage.value == 70.0
            assert summary.scorecards.kpi_score.value == 3.5
            assert summary.task_diagnostics.total == 2
            assert summary.task_diagnostics.completed == 1
            assert summary.task_diagnostics.pending == 1
            assert summary.approval_diagnostics.total_pending_approval == 1
            assert summary.notifications.unread_count == 1
            assert len(summary.notifications.recent) == 2
            assert summary.activity_summary.total == 1
            assert len(summary.department_comparison) == 1
            comparison = summary.department_comparison[0]
            assert comparison.department_id == dept.id
            assert comparison.evaluated_count == 2
            assert comparison.kpi_percentage.value == 70.0
            # Trend labels come from the period range, in order.
            assert summary.trends["kpi_percentage"].labels
            # Status distribution: same buckets and counts (the query's
            # own ORDER BY decides row order, and it is unchanged).
            assert {
                (item.status, item.count)
                for item in summary.kpi_status_distribution
            } == {("VERY_GOOD", 1), ("GOOD", 1)}

    _run(case())


def test_overview_payload_shape_and_values(tmp_path):
    async def case():
        async for db, sa, emp, dept, period in _world(
            tmp_path / "overview.sqlite"
        ):
            overview = await build_executive_overview(
                db,
                scope_user_ids=None,
                department_ids=None,
                show_departments=True,
                period_id=None,
                period_start=datetime(2026, 10, 1, tzinfo=UTC),
                period_end=datetime(2026, 11, 1, tzinfo=UTC),
            )
            assert overview.selected_period is not None
            assert overview.selected_period.id == period.id
            # Date-range summary: 2 evaluated employees, avg 70.
            assert overview.summary.evaluated_employee_count == 2
            assert overview.summary.average_percentage == 70.0
            assert overview.summary.active_employee_count == 2
            assert overview.summary.no_result_employee_count == 0
            assert len(overview.departments) == 1
            assert overview.departments[0].department_id == dept.id
            assert overview.departments[0].evaluated_employee_count == 2

    _run(case())


def test_gather_runs_jobs_concurrently_and_keeps_order(tmp_path):
    async def case():
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path}/gather.sqlite"
        )
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                started, finished = [], []

                async def job(session, index):
                    started.append((index, asyncio.get_running_loop().time()))
                    await asyncio.sleep(0.2)
                    finished.append(
                        (index, asyncio.get_running_loop().time())
                    )
                    return index * 10

                results = await gather_on_sessions(
                    db, [lambda s, i=i: job(s, i) for i in range(4)]
                )
                assert results == [0, 10, 20, 30]
                # Every job started before any job finished: genuine
                # overlap, not four sequential 0.2s sleeps.
                assert max(t for _, t in started) < min(
                    t for _, t in finished
                )
                # And the wall time is one sleep, not four.
                wall = max(t for _, t in finished) - min(
                    t for _, t in started
                )
                assert wall < 0.45
        finally:
            await engine.dispose()

    _run(case())


def test_gather_empty_and_error(tmp_path):
    async def case():
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{tmp_path}/gather2.sqlite"
        )
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as db:
                assert await gather_on_sessions(db, []) == []

                async def failing(session):
                    raise ValueError("boom")

                async def ok(session):
                    return 7

                with pytest.raises(ValueError, match="boom"):
                    await gather_on_sessions(db, [failing, ok])
        finally:
            await engine.dispose()

    _run(case())
