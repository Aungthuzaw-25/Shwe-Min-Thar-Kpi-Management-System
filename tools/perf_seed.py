"""Populate only the explicitly marked disposable performance database."""

from __future__ import annotations

import argparse
import asyncio
import os
import random
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.security import get_password_hash
from app.modules.departments.models import Department, UserDepartment
from app.modules.kpi.models import (
    FormulaScope,
    FormulaStatus,
    KPIAssignment,
    KpiCategory,
    KPIFormula,
    KPIPeriod,
    KPIPeriodStatus,
    KPIRecord,
    KpiRule,
    KPIStatus,
    PeriodType,
)
from app.modules.notifications.models import Notification
from app.modules.tasks.models.task import Task, TaskFrequency, TaskStatus
from app.modules.users.models import Position, Role, User

DATABASE_MARKER = "kpi-perf-disposable-v1"
DB_HOST = "postgres-qa"
DB_PORT = 5432
DB_NAME = "kpi_qa"
RANDOM_SEED = 20261012
EMAIL_PREFIX = "perf.user."
OBJECT_PREFIX = "PERF-SEED:"
LEVELS = ("L0", "L1", "L2")
TASK_STATUSES = tuple(TaskStatus)
TASK_FREQUENCIES = tuple(TaskFrequency)
KPI_STATUSES = tuple(KPIStatus)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=int, default=60, help="Total users including QA fixture users.")
    parser.add_argument("--tasks", type=int, default=5000)
    parser.add_argument("--notifications", type=int, default=3000)
    parser.add_argument("--scale", type=int, choices=(1, 3), default=1)
    parser.add_argument("--random-seed", type=int, default=RANDOM_SEED)
    args = parser.parse_args()
    if min(args.users, args.tasks, args.notifications) < 1:
        parser.error("users, tasks, and notifications must be positive.")
    return args


def _require_disposable_database() -> str:
    if settings.ENV.lower() != "qa":
        raise RuntimeError("Performance seed requires ENV=qa.")

    database_url = settings.DATABASE_URL
    parsed = urlparse(database_url)
    if (
        parsed.scheme != "postgresql+asyncpg"
        or (parsed.hostname or "").lower() != DB_HOST
        or parsed.port != DB_PORT
        or parsed.path.lstrip("/") != DB_NAME
    ):
        raise RuntimeError(
            "Refusing to seed: DATABASE_URL must target the isolated "
            "postgres-qa:5432/kpi_qa disposable service."
        )

    confirmation = os.getenv("PERF_SEED_CONFIRMATION")
    if confirmation != DATABASE_MARKER:
        raise RuntimeError("Refusing to seed: disposable database confirmation is missing.")

    if not os.getenv("QA_SEED_PASSWORD", "").strip():
        raise RuntimeError("Set QA_SEED_PASSWORD to the configured local test password.")
    return database_url


def _periods() -> list[tuple[str, PeriodType, datetime, datetime, KPIPeriodStatus]]:
    months: list[tuple[str, PeriodType, datetime, datetime, KPIPeriodStatus]] = []
    year, month = 2025, 11
    for _ in range(12):
        start = datetime(year, month, 1, tzinfo=UTC)
        if month == 12:
            end = datetime(year + 1, 1, 1, tzinfo=UTC) - timedelta(seconds=1)
        else:
            end = datetime(year, month + 1, 1, tzinfo=UTC) - timedelta(seconds=1)
        months.append(
            (
                f"PERF-SEED: Monthly {year}-{month:02d}",
                PeriodType.MONTHLY,
                start,
                end,
                KPIPeriodStatus.FINALIZED,
            )
        )
        month += 1
        if month == 13:
            year, month = year + 1, 1

    return [
        *months,
        (
            "PERF-SEED: Quarterly 2026-Q3",
            PeriodType.QUARTERLY,
            datetime(2026, 7, 1, tzinfo=UTC),
            datetime(2026, 9, 30, 23, 59, 59, tzinfo=UTC),
            KPIPeriodStatus.FINALIZED,
        ),
        (
            "PERF-SEED: Yearly 2025",
            PeriodType.YEARLY,
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC),
            KPIPeriodStatus.FINALIZED,
        ),
    ]


async def _assert_marker(engine) -> None:
    async with engine.connect() as connection:
        result = await connection.execute(
            text("SELECT marker FROM perf_database_guard WHERE singleton IS TRUE")
        )
        marker = result.scalar_one_or_none()
    if marker != DATABASE_MARKER:
        raise RuntimeError(
            "Refusing to seed: database-side disposable marker is absent or incorrect."
        )


async def _clear_previous_seed(session) -> None:
    perf_users = select(User.id).where(User.email.like(f"{EMAIL_PREFIX}%"))
    perf_formulas = select(KPIFormula.id).where(KPIFormula.name.like(f"{OBJECT_PREFIX}%"))

    await session.execute(
        delete(KPIRecord).where(KPIRecord.formula_id.in_(perf_formulas))
    )
    await session.execute(
        delete(KPIAssignment).where(KPIAssignment.kpi_formula_id.in_(perf_formulas))
    )
    await session.execute(delete(KPIFormula).where(KPIFormula.id.in_(perf_formulas)))
    await session.execute(
        delete(KPIPeriod).where(KPIPeriod.label.like(f"{OBJECT_PREFIX}%"))
    )
    await session.execute(
        delete(Task).where(Task.title.like(f"{OBJECT_PREFIX}%"))
    )
    await session.execute(
        delete(Notification).where(Notification.title.like(f"{OBJECT_PREFIX}%"))
    )
    await session.execute(
        delete(KpiRule).where(KpiRule.category_name.like(f"{OBJECT_PREFIX}%"))
    )
    await session.execute(
        delete(KpiCategory).where(KpiCategory.name.like(f"{OBJECT_PREFIX}%"))
    )
    await session.execute(
        delete(UserDepartment).where(UserDepartment.user_id.in_(perf_users))
    )
    await session.execute(delete(User).where(User.email.like(f"{EMAIL_PREFIX}%")))
    await session.execute(
        delete(Position).where(Position.name.like(f"{OBJECT_PREFIX}%"))
    )
    await session.execute(
        delete(Department).where(Department.name.like(f"{OBJECT_PREFIX}%"))
    )
    await session.flush()


async def _insert_batches(session, model, rows: list, batch_size: int = 500) -> None:
    for offset in range(0, len(rows), batch_size):
        session.add_all(rows[offset : offset + batch_size])
        await session.flush()


async def seed(args: argparse.Namespace) -> dict[str, int]:
    database_url = _require_disposable_database()
    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"statement_cache_size": 0},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _assert_marker(engine)
        rng = random.Random(args.random_seed)  # noqa: S311
        final_user_count = args.users * args.scale
        task_count = args.tasks * args.scale
        notification_count = args.notifications * args.scale
        periods_spec = _periods()
        password_hash = get_password_hash(os.environ["QA_SEED_PASSWORD"])

        async with factory() as session:
            await _clear_previous_seed(session)

            roles = {
                role.name: role
                for role in (await session.execute(select(Role))).scalars().all()
            }
            required_roles = {"Super Admin", "Admin", "HR", "Department User"}
            if missing := required_roles.difference(roles):
                raise RuntimeError(f"Required application roles are missing: {sorted(missing)}")

            qa_users = list(
                (await session.execute(select(User).order_by(User.email))).scalars().all()
            )
            if len(qa_users) > final_user_count:
                raise RuntimeError(
                    f"The disposable QA fixture already has {len(qa_users)} users; "
                    f"requested total is {final_user_count}."
                )

            qa_departments = list(
                (await session.execute(select(Department).order_by(Department.name)))
                .scalars()
                .all()
            )
            qa_department = next(
                (department for department in qa_departments if department.name.startswith("QA ")),
                None,
            )
            if qa_department is None:
                raise RuntimeError("Run the existing QA seed before the performance seed.")

            departments = [
                Department(name=f"{OBJECT_PREFIX} Department {index:02d}", priority=index)
                for index in range(1, 7)
            ]
            session.add_all(departments)
            await session.flush()

            category_names = [
                f"{OBJECT_PREFIX} Operational Quality {index}"
                for index in range(1, 5)
            ]
            categories = []
            for category_name in category_names:
                category = (
                    await session.execute(
                        select(KpiCategory).where(KpiCategory.name == category_name)
                    )
                ).scalar_one_or_none()
                if category is None:
                    category = KpiCategory(name=category_name)
                    session.add(category)
                categories.append(category)
            await session.flush()
            category_ids = {category.name: category.id for category in categories}

            position_by_department_level: dict[tuple[str, str], Position] = {}
            for department in departments:
                for level in LEVELS:
                    position = Position(
                        name=f"{OBJECT_PREFIX} {department.name[-2:]} {level}",
                        department_id=department.id,
                        is_active=True,
                    )
                    session.add(position)
                    position_by_department_level[(department.name, level)] = position
            await session.flush()

            generated_users: list[User] = []
            generated_levels: dict[str, str] = {}
            generated_departments: dict[str, Department] = {}
            missing_users = final_user_count - len(qa_users)
            for index in range(missing_users):
                level = LEVELS[index % len(LEVELS)]
                department = departments[index % len(departments)]
                position = position_by_department_level[(department.name, level)]
                email = f"{EMAIL_PREFIX}{index + 1:04d}@perf.example.com"
                user = User(
                    email=email,
                    hashed_password=password_hash,
                    local_password_enabled=True,
                    first_name=f"Perf{index + 1:04d}",
                    last_name=level,
                    position_id=position.id,
                    role_id=roles["Department User"].id,
                    is_active=True,
                    is_locked=False,
                    must_change_password=False,
                )
                generated_users.append(user)
                generated_levels[email] = level
                generated_departments[email] = department
            session.add_all(generated_users)
            await session.flush()

            memberships = []
            for user in generated_users:
                level = generated_levels[user.email]
                department = generated_departments[user.email]
                memberships.append(
                    UserDepartment(
                        user_id=user.id,
                        department_id=department.id,
                        user_level=level,
                    )
                )
            await _insert_batches(session, UserDepartment, memberships)

            users = [*qa_users, *generated_users]
            qa_levels = {}
            qa_position_by_level = {}
            for user in qa_users:
                membership = (
                    await session.execute(
                        select(UserDepartment)
                        .where(
                            UserDepartment.user_id == user.id,
                            UserDepartment.department_id == qa_department.id,
                            UserDepartment.is_deleted.is_(False),
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
                qa_levels[user.id] = membership.user_level if membership else "L2"
                if membership and user.position_id:
                    position = await session.get(Position, user.position_id)
                    if position is not None:
                        qa_position_by_level[membership.user_level] = position

            for level in LEVELS:
                leader = next(
                    (
                        user
                        for user in generated_users
                        if generated_levels[user.email] == level
                        and generated_departments[user.email] == departments[0]
                    ),
                    None,
                )
                if leader:
                    departments[0].head_id = leader.id

            period_rows = [
                KPIPeriod(
                    label=label,
                    start_date=start,
                    end_date=end,
                    status=status,
                )
                for label, _period_type, start, end, status in periods_spec
            ]
            session.add_all(period_rows)
            await session.flush()

            formula_by_department_level: dict[tuple[str, str], KPIFormula] = {}
            all_departments = [qa_department, *departments]
            rules = []
            for department in all_departments:
                for level in LEVELS:
                    position = (
                        next(
                            (
                                position
                                for position in position_by_department_level.values()
                                if position.department_id == department.id
                                and position.name.endswith(f" {level}")
                            ),
                            None,
                        )
                        if department != qa_department
                        else qa_position_by_level.get(level)
                    )
                    category_payload = [
                        {
                            "name": category_names[category],
                            "description": (
                                f"Disposable performance benchmark category {category + 1}"
                            ),
                            "weight": 0.25,
                            "max_score": 5.0,
                            "evaluation_type": "TARGET",
                            "deductions": [],
                        }
                        for category in range(4)
                    ]
                    formula = KPIFormula(
                        name=f"{OBJECT_PREFIX} {department.name} {level}",
                        description="Deterministic disposable performance fixture.",
                        expression="weighted_category_score",
                        department_id=department.id,
                        position_id=position.id if position else None,
                        position_name=position.name if position else None,
                        user_level=level,
                        categories=category_payload,
                        scope=FormulaScope.ROLE_LEVEL,
                        is_active=True,
                        status=FormulaStatus.ACTIVE,
                        version=1,
                        effective_month="2026-09",
                        created_by_id=qa_users[0].id if qa_users else None,
                    )
                    session.add(formula)
                    formula_by_department_level[(department.name, level)] = formula
                    if position is not None:
                        rules.extend(
                            KpiRule(
                                department_id=department.id,
                                position_id=position.id,
                                level=level,
                                category_name=category_name,
                                weight=0.25,
                                is_active=True,
                            )
                            for category_name in category_names
                        )
            await session.flush()
            await _insert_batches(session, KpiRule, rules)

            period_assignments: list[KPIAssignment] = []
            kpi_records: list[KPIRecord] = []
            for user in users:
                if user in generated_users:
                    level = generated_levels[user.email]
                    department = generated_departments[user.email]
                else:
                    level = qa_levels[user.id]
                    department = qa_department

                formula = formula_by_department_level[(department.name, level)]
                position_id = user.position_id or formula.position_id
                for period, (_label, period_type, start, end, _status) in zip(
                    period_rows, periods_spec, strict=True
                ):
                    period_assignments.append(
                        KPIAssignment(
                            department_id=department.id,
                            position_id=position_id,
                            user_id=user.id,
                            kpi_formula_id=formula.id,
                            kpi_period_id=period.id,
                            assigned_by_id=qa_users[0].id if qa_users else None,
                            assigned_at=start.replace(tzinfo=None),
                        )
                    )
                    raw_score = round(rng.uniform(2.1, 5.0), 2)
                    percentage = round(raw_score / 5 * 100, 2)
                    status_bucket = (
                        KPIStatus.EXCELLENT if percentage >= 90 else
                        KPIStatus.VERY_GOOD if percentage >= 80 else
                        KPIStatus.GOOD if percentage >= 60 else
                        KPIStatus.FAIR if percentage >= 40 else KPIStatus.POOR
                    )
                    kpi_records.append(
                        KPIRecord(
                            user_id=user.id,
                            department_id=department.id,
                            formula_id=formula.id,
                            kpi_period_id=period.id,
                            period_type=period_type,
                            period_start=start,
                            period_end=end,
                            raw_score=raw_score,
                            weighted_score=percentage,
                            percentage=percentage,
                            status_bucket=status_bucket,
                            is_frozen=True,
                        )
                    )
            await _insert_batches(session, KPIAssignment, period_assignments)
            await _insert_batches(session, KPIRecord, kpi_records)

            now = datetime.now(UTC).replace(tzinfo=None)
            generated_tasks = []
            for index in range(task_count):
                owner = users[index % len(users)]
                supervisor = qa_users[0] if qa_users else owner
                due_date = now + timedelta(days=rng.randint(-30, 30))
                status = TASK_STATUSES[index % len(TASK_STATUSES)]
                generated_tasks.append(
                    Task(
                        title=f"{OBJECT_PREFIX} Task {index + 1:05d}",
                        description=f"Seeded task for deterministic performance profiling #{index + 1}.",
                        category=category_names[index % len(category_names)],
                        category_id=category_ids[category_names[index % len(category_names)]],
                        status=status,
                        due_date=due_date,
                        grace_period_days=index % 4,
                        is_late=(index % 7 == 0),
                        late_days=index % 6,
                        owner_id=owner.id,
                        assigner_id=supervisor.id,
                        version=1,
                        frequency=TASK_FREQUENCIES[index % len(TASK_FREQUENCIES)],
                        is_escalated=(index % 19 == 0),
                        escalated_to_level="L0" if index % 19 == 0 else None,
                        created_at=now - timedelta(days=rng.randint(0, 365)),
                    )
                )
            await _insert_batches(session, Task, generated_tasks)

            notifications = [
                Notification(
                    user_id=users[index % len(users)].id,
                    title=f"{OBJECT_PREFIX} Notification {index + 1:05d}",
                    message=f"Seeded notification for deterministic profiling #{index + 1}.",
                    notification_type=("REMINDER", "DEADLINE", "APPROVAL")[index % 3],
                    is_read=index % 4 == 0,
                    channel="DASHBOARD",
                    created_at=now - timedelta(days=rng.randint(0, 180)),
                )
                for index in range(notification_count)
            ]
            await _insert_batches(session, Notification, notifications)
            await session.commit()

            return {
                "users_total": len(users),
                "users_generated": len(generated_users),
                "departments_generated": len(departments),
                "positions_generated": len(position_by_department_level),
                "kpi_periods": len(period_rows),
                "kpi_formulas": len(formula_by_department_level),
                "kpi_assignments": len(period_assignments),
                "kpi_records": len(kpi_records),
                "tasks": len(generated_tasks),
                "notifications": len(notifications),
            }
    finally:
        await engine.dispose()


async def main() -> None:
    args = _parse_args()
    counts = await seed(args)
    for name, count in counts.items():
        print(f"{name}={count}")


if __name__ == "__main__":
    asyncio.run(main())
