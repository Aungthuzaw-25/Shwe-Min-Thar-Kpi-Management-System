import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.modules.departments.models import Department, UserDepartment
from app.modules.kpi.dashboard_schemas import (
    ActivityActionCount,
    ActivitySummary,
    ApprovalDiagnostics,
    CategoryPerformance,
    DashboardAlert,
    DashboardDataQuality,
    DashboardMetric,
    DashboardNotifications,
    DashboardPeriod,
    DashboardScope,
    DashboardScorecards,
    DashboardSummaryResponse,
    DashboardTrend,
    DepartmentComparison,
    KPIStatusCount,
    TaskDiagnostics,
)
from app.modules.kpi.models import (
    KPIPeriod,
    KPIPeriodStatus,
    KPIRecord,
    PeriodType,
)
from app.modules.kpi.period_ranges import normalize_calendar_range
from app.modules.notifications.models import Notification
from app.modules.notifications.repo import (
    count_unread_notifications,
    get_notifications_by_user,
)
from app.modules.tasks.models.activity_timeline import ActivityTimeline
from app.modules.tasks.models.task import Task, TaskStatus
from app.modules.users.models import User
from app.services.activity_service import activity_payload, get_activity_scope_user_ids

logger = logging.getLogger(__name__)

# A non-zero operational exception is actionable in this first intelligence
# layer. No synthetic score or statistical threshold is applied.
ALERT_COUNT_THRESHOLD = 1
PERSISTED_KPI_PERIOD_STATUSES = (
    KPIPeriodStatus.FINALIZED,
    KPIPeriodStatus.LOCKED,
)


def _metric(value, *, count: int | None = None) -> DashboardMetric:
    if count == 0 or value is None:
        return DashboardMetric(value=None, status="unavailable")
    return DashboardMetric(value=round(float(value), 2), status="available")


def _normalize_level(value) -> str | None:
    normalized = str(value or "").strip().upper().replace("LEVEL ", "")
    return normalized or None


def _period_filters(
    statement,
    period_column_type,
    period_start_column,
    period_end_column,
    period_type: PeriodType,
    period_start: datetime | None,
    period_end: datetime | None,
):
    """Filter records fully contained in the requested calendar range."""
    statement = statement.where(period_column_type == period_type)
    if period_start is not None:
        statement = statement.where(period_start_column >= period_start)
    if period_end is not None:
        statement = statement.where(period_end_column <= period_end)
    return statement


def _deduplicated_kpi_records():
    row_number = func.row_number().over(
        partition_by=[
            KPIRecord.user_id,
            KPIRecord.formula_id,
            KPIRecord.period_type,
            KPIRecord.period_start,
            KPIRecord.period_end,
        ],
        order_by=[
            KPIRecord.updated_at.desc().nulls_last(),
            KPIRecord.created_at.desc(),
            KPIRecord.id.desc(),
        ],
    ).label("record_rank")
    return select(
        KPIRecord.id,
        KPIRecord.user_id,
        KPIRecord.department_id,
        KPIRecord.period_type,
        KPIRecord.period_start,
        KPIRecord.period_end,
        KPIRecord.weighted_score,
        KPIRecord.percentage,
        KPIRecord.status_bucket,
        row_number,
    ).outerjoin(
        KPIPeriod,
        KPIPeriod.id == KPIRecord.kpi_period_id,
    ).where(
        KPIRecord.is_deleted.is_(False),
        or_(
            KPIRecord.kpi_period_id.is_(None),
            KPIPeriod.status.in_(PERSISTED_KPI_PERIOD_STATUSES),
        ),
    ).subquery()


def _finalized_kpi_records(
    period_type: PeriodType,
    period_start: datetime | None,
    period_end: datetime | None,
):
    """Return one authoritative KPI record per user/formula/period in range."""
    records = _deduplicated_kpi_records()
    statement = select(records).where(records.c.record_rank == 1)
    return _period_filters(
        statement,
        records.c.period_type,
        records.c.period_start,
        records.c.period_end,
        period_type,
        period_start,
        period_end,
    ).subquery()


async def _build_kpi_aggregates(
    db: AsyncSession,
    user_ids: list[UUID],
    department_ids: list[UUID] | None,
    period_type: PeriodType,
    period_start: datetime | None,
    period_end: datetime | None,
):
    records = _finalized_kpi_records(period_type, period_start, period_end)
    filters = [
        records.c.user_id.in_(user_ids)
        if user_ids
        else records.c.user_id == UUID(int=0)
    ]
    overall = (
        await db.execute(
            select(
                func.avg(records.c.weighted_score).label("score"),
                func.avg(records.c.percentage).label("percentage"),
                func.count(records.c.id).label("count"),
            ).where(*filters)
        )
    ).one()

    comparisons: list[DepartmentComparison] = []
    if department_ids:
        department_statement = (
            select(
                Department.id,
                Department.name,
                func.count(func.distinct(UserDepartment.user_id)).label("employee_count"),
                func.avg(records.c.weighted_score).label("score"),
                func.avg(records.c.percentage).label("percentage"),
                func.count(records.c.id).label("evaluated_count"),
            )
            .join(
                UserDepartment,
                and_(
                    UserDepartment.department_id == Department.id,
                    UserDepartment.is_deleted.is_(False),
                ),
            )
            .outerjoin(
                records,
                and_(
                    records.c.user_id == UserDepartment.user_id,
                    records.c.department_id == Department.id,
                ),
            )
            .where(
                Department.id.in_(department_ids),
                Department.is_deleted.is_(False),
                UserDepartment.user_id.in_(user_ids),
            )
            .group_by(Department.id, Department.name)
            .order_by(
                func.avg(records.c.percentage).desc().nulls_last(),
                func.count(records.c.id).desc(),
                Department.name,
            )
        )
        for row in (await db.execute(department_statement)).all():
            comparisons.append(
                DepartmentComparison(
                    department_id=row.id,
                    department_name=row.name,
                    employee_count=int(row.employee_count or 0),
                    kpi_score=_metric(row.score, count=int(row.evaluated_count or 0)),
                    kpi_percentage=_metric(
                        row.percentage, count=int(row.evaluated_count or 0)
                    ),
                    evaluated_count=int(row.evaluated_count or 0),
                )
            )
    return overall, comparisons


async def _build_kpi_trend(
    db: AsyncSession,
    user_ids: list[UUID],
    period_type: PeriodType,
    period_start: datetime | None,
    period_end: datetime | None,
) -> DashboardTrend:
    records = _finalized_kpi_records(period_type, period_start, period_end)
    filters = [
        records.c.user_id.in_(user_ids)
        if user_ids
        else records.c.user_id == UUID(int=0)
    ]
    rows = (
        await db.execute(
            select(
                records.c.period_start.label("period_start"),
                func.avg(records.c.percentage).label("percentage"),
            )
            .where(*filters)
            .group_by(records.c.period_start)
            .order_by(records.c.period_start)
        )
    ).all()
    return DashboardTrend(
        labels=[row.period_start.isoformat() for row in rows],
        values=[
            round(float(row.percentage), 2)
            if row.percentage is not None
            else None
            for row in rows
        ],
        count=len(rows),
        current_value=(
            round(float(rows[-1].percentage), 2)
            if rows and rows[-1].percentage is not None
            else None
        ),
        previous_value=(
            round(float(rows[-2].percentage), 2)
            if len(rows) > 1 and rows[-2].percentage is not None
            else None
        ),
        delta=(
            round(float(rows[-1].percentage - rows[-2].percentage), 2)
            if len(rows) > 1
            and rows[-1].percentage is not None
            and rows[-2].percentage is not None
            else None
        ),
        comparison_status=_comparison_status(len(rows)),
    )


def _comparison_status(row_count: int) -> str:
    if row_count > 1:
        return "available"
    if row_count == 1:
        return "insufficient_data"
    return "unavailable"


async def _build_kpi_status_distribution(
    db: AsyncSession,
    user_ids: list[UUID],
    period_type: PeriodType,
    period_start: datetime | None,
    period_end: datetime | None,
) -> list[KPIStatusCount]:
    """Group finalized KPI records by their persisted status bucket."""
    records = _finalized_kpi_records(period_type, period_start, period_end)
    filters = [
        records.c.user_id.in_(user_ids)
        if user_ids
        else records.c.user_id == UUID(int=0)
    ]
    rows = (
        await db.execute(
            select(
                records.c.status_bucket.label("status_bucket"),
                func.count(records.c.id).label("count"),
            )
            .where(*filters)
            .group_by(records.c.status_bucket)
            .order_by(records.c.status_bucket)
        )
    ).all()
    return [
        KPIStatusCount(
            status=getattr(row.status_bucket, "value", str(row.status_bucket)),
            count=int(row.count or 0),
        )
        for row in rows
    ]


async def _build_activity_data(
    db: AsyncSession,
    current_user: User,
    department_user_ids: list[UUID] | None = None,
) -> tuple[list[dict], ActivitySummary]:
    filters = [ActivityTimeline.is_deleted.is_(False)]
    if department_user_ids is not None:
        filters.append(ActivityTimeline.user_id.in_(department_user_ids))
    else:
        actor_ids = await get_activity_scope_user_ids(db, current_user)
        if actor_ids is not None:
            filters.append(ActivityTimeline.user_id.in_(actor_ids))

    recent_statement = (
        select(ActivityTimeline)
        .options(joinedload(ActivityTimeline.user))
        .where(*filters)
        .order_by(ActivityTimeline.created_at.desc())
        .limit(10)
    )
    activities = (await db.execute(recent_statement)).scalars().all()
    total = int(
        (
            await db.execute(select(func.count(ActivityTimeline.id)).where(*filters))
        ).scalar_one()
        or 0
    )
    action_rows = (
        await db.execute(
            select(
                ActivityTimeline.action,
                func.count(ActivityTimeline.id).label("count"),
            )
            .where(*filters)
            .group_by(ActivityTimeline.action)
            .order_by(func.count(ActivityTimeline.id).desc(), ActivityTimeline.action)
            .limit(10)
        )
    ).all()
    return (
        [activity_payload(item, item.user) for item in activities],
        ActivitySummary(
            total=total,
            by_action=[
                ActivityActionCount(action=row.action, count=int(row.count or 0))
                for row in action_rows
            ],
        ),
    )
async def _build_task_diagnostics(
    db: AsyncSession, user_ids: list[UUID]
) -> tuple[TaskDiagnostics, ApprovalDiagnostics]:
    row = (await db.execute(_task_diagnostic_statement(user_ids))).one()
    return _task_diagnostics_from_row(row), _approval_diagnostics_from_row(row)


def _task_diagnostic_statement(user_ids: list[UUID]):
    filters = [Task.is_deleted.is_(False), _task_owner_filter(user_ids)]
    now = datetime.now(UTC)
    return select(
        func.count(Task.id).label("total"),
        func.count(Task.id).filter(Task.status == TaskStatus.DRAFT).label("draft"),
        func.count(
            Task.id
        ).filter(
            Task.status.in_([TaskStatus.PENDING_L1, TaskStatus.PENDING_L0])
        ).label("pending"),
        func.count(Task.id).filter(Task.status == TaskStatus.PENDING_L1).label("pending_l1"),
        func.count(Task.id).filter(Task.status == TaskStatus.PENDING_L0).label("pending_l0"),
        func.count(Task.id).filter(Task.status == TaskStatus.APPROVED).label("approved"),
        func.count(Task.id).filter(Task.status == TaskStatus.COMPLETED).label("completed"),
        func.count(Task.id).filter(Task.status == TaskStatus.REJECTED).label("rejected"),
        func.count(Task.id).filter(
            Task.due_date.is_not(None),
            Task.due_date < now,
            Task.status.not_in(
                [TaskStatus.COMPLETED, TaskStatus.APPROVED, TaskStatus.REJECTED]
            ),
        ).label("overdue"),
        func.count(Task.id).filter(Task.is_late.is_(True)).label("late"),
    ).where(*filters)


def _task_owner_filter(user_ids: list[UUID]):
    if user_ids:
        return Task.owner_id.in_(user_ids)
    return Task.owner_id == UUID(int=0)


def _task_diagnostics_from_row(row) -> TaskDiagnostics:
    total = int(row.total or 0)
    completed = int(row.completed or 0)
    return TaskDiagnostics(
        total=total,
        draft=int(row.draft or 0),
        pending=int(row.pending or 0),
        pending_approval=int(row.pending or 0),
        approved=int(row.approved or 0),
        completed=completed,
        rejected=int(row.rejected or 0),
        overdue=int(row.overdue or 0),
        late=int(row.late or 0),
        completion_percentage=_metric(
            completed / total * 100 if total else None, count=total
        ),
        overdue_rate=_metric(
            int(row.overdue or 0) / total * 100 if total else None,
            count=total,
        ),
        late_rate=_metric(
            int(row.late or 0) / total * 100 if total else None,
            count=total,
        ),
    )


def _approval_diagnostics_from_row(row) -> ApprovalDiagnostics:
    return ApprovalDiagnostics(
        pending_l1=int(row.pending_l1 or 0),
        pending_l0=int(row.pending_l0 or 0),
        total_pending_approval=int(row.pending or 0),
        rejected=int(row.rejected or 0),
    )


async def _dashboard_scope(
    db: AsyncSession, current_user: User, department_ids: list[UUID] | None
) -> tuple[str, str | None, list[UUID], list[UUID]]:
    role_name = str(getattr(getattr(current_user, "role", None), "name", "") or "")
    normalized_role_name = role_name.replace("-", " ").replace("_", " ").upper()
    memberships = (
        await db.execute(
            select(UserDepartment.user_level).where(
                UserDepartment.user_id == current_user.id,
                UserDepartment.is_deleted.is_(False),
            )
        )
    ).scalars().all()
    level = _normalize_level(memberships[0] if memberships else None)
    visible_departments = department_ids or []
    is_department_comparison_visible = (
        normalized_role_name in {"SUPER ADMIN", "ADMIN", "HR"}
        or level in {"L0", "L1"}
    )
    comparison_department_ids = (
        visible_departments if is_department_comparison_visible else []
    )
    return role_name, level, visible_departments, comparison_department_ids


def _dashboard_alerts(
    task_diagnostics: TaskDiagnostics,
    approval_diagnostics: ApprovalDiagnostics,
    period_type: PeriodType,
) -> list[DashboardAlert]:
    alerts = []
    if task_diagnostics.overdue >= ALERT_COUNT_THRESHOLD:
        alerts.append(
            DashboardAlert(
                severity="warning",
                type="overdue_tasks",
                message="Tasks are overdue",
                count=task_diagnostics.overdue,
                source="tasks",
                period=period_type.value,
            )
        )
    if task_diagnostics.late >= ALERT_COUNT_THRESHOLD:
        alerts.append(
            DashboardAlert(
                severity="warning",
                type="late_tasks",
                message="Tasks were submitted late",
                count=task_diagnostics.late,
                source="tasks",
                period=period_type.value,
            )
        )
    if approval_diagnostics.total_pending_approval >= ALERT_COUNT_THRESHOLD:
        alerts.append(
            DashboardAlert(
                severity="info",
                type="pending_approvals",
                message="Tasks are awaiting approval",
                count=approval_diagnostics.total_pending_approval,
                source="approvals",
                period=period_type.value,
            )
        )
    return alerts


def _dashboard_data_quality(
    kpi_row,
    task_diagnostics: TaskDiagnostics,
    kpi_trend: DashboardTrend,
    comparisons: list[DepartmentComparison],
) -> DashboardDataQuality:
    evaluated_count = int(kpi_row.count or 0)
    unavailable_metrics = [
        item
        for item, unavailable in (
            ("kpi_records", not bool(kpi_row.count)),
            ("kpi_trend", kpi_trend.count < 2),
            ("department_comparison", not bool(comparisons)),
            ("category_performance", True),
            ("task_data", not bool(task_diagnostics.total)),
            ("target_data", True),
            ("attendance_intelligence", True),
        )
        if unavailable
    ]
    return DashboardDataQuality(
        status="available" if kpi_row.count and task_diagnostics.total else "partial",
        evaluated_kpi_records=evaluated_count,
        kpi_trend="available" if kpi_trend.count else "unavailable",
        department_comparison="available" if comparisons else "unavailable",
        category_performance=CategoryPerformance(
            status="unavailable",
            reason="category_results_not_independently_stored",
        ),
        task_data="available" if task_diagnostics.total else "unavailable",
        comparison=kpi_trend.comparison_status,
        period_notes=[
            "Task and approval diagnostics are lifecycle-scoped because tasks "
            "do not store KPI period boundaries."
        ],
        metrics_unavailable=unavailable_metrics,
        warnings=[
            "Category-level KPI performance is unavailable because finalized "
            "category results are not independently stored.",
            "Target versus actual is unavailable because scoring-rule targets "
            "are category thresholds, not authoritative aggregate targets.",
            "Attendance intelligence is unavailable because no authoritative "
            "attendance result source is integrated.",
        ],
    )


async def build_dashboard_summary(
    db: AsyncSession,
    current_user: User,
    *,
    scope_user_ids: list[UUID],
    department_ids: list[UUID] | None,
    period_type: PeriodType,
    period_start: datetime | None,
    period_end: datetime | None,
    department_filtered: bool = False,
) -> DashboardSummaryResponse:
    period_range = normalize_calendar_range(period_start, period_end)
    period_start = period_range.start_inclusive
    period_end = period_range.end_exclusive

    (
        role_name,
        level,
        visible_departments,
        comparison_department_ids,
    ) = await _dashboard_scope(
        db, current_user, department_ids
    )
    kpi_row, comparisons = await _build_kpi_aggregates(
        db,
        scope_user_ids,
        comparison_department_ids,
        period_type,
        period_start,
        period_end,
    )
    task_diagnostics, approval_diagnostics = await _build_task_diagnostics(
        db, scope_user_ids
    )
    kpi_trend = await _build_kpi_trend(
        db, scope_user_ids, period_type, period_start, period_end
    )
    kpi_status_distribution = await _build_kpi_status_distribution(
        db, scope_user_ids, period_type, period_start, period_end
    )

    department_count = len(visible_departments)
    if department_ids is None:
        department_count = int(
            (
                await db.execute(
                    select(func.count(Department.id)).where(
                        Department.is_deleted.is_(False)
                    )
                )
            ).scalar_one()
        )
    scorecards = DashboardScorecards(
        kpi_score=_metric(kpi_row.score, count=int(kpi_row.count or 0)),
        kpi_percentage=_metric(kpi_row.percentage, count=int(kpi_row.count or 0)),
        evaluated_kpi_records=int(kpi_row.count or 0),
        employee_count=len(scope_user_ids),
        department_count=department_count,
        task_completion_percentage=task_diagnostics.completion_percentage,
        pending_approvals=approval_diagnostics.total_pending_approval,
    )

    activity, activity_summary = await _build_activity_data(
        db,
        current_user,
        scope_user_ids if department_filtered else None,
    )

    if department_filtered:
        notification_filters = [
            Notification.is_deleted.is_(False),
            Notification.user_id.in_(scope_user_ids),
        ]
        notifications = await get_notifications_by_user(
            db, current_user.id, limit=5
        )
        unread_count = int(
            (
                await db.execute(
                    select(func.count(Notification.id)).where(
                        *notification_filters,
                        Notification.is_read.is_(False),
                    )
                )
            ).scalar_one()
            or 0
        )
    else:
        notifications = await get_notifications_by_user(
            db, current_user.id, limit=5
        )
        unread_count = await count_unread_notifications(db, current_user.id)
    alerts = _dashboard_alerts(
        task_diagnostics, approval_diagnostics, period_type
    )

    return DashboardSummaryResponse(
        scope=DashboardScope(
            role=role_name,
            level=level,
            department_ids=visible_departments,
            user_count=len(scope_user_ids),
        ),
        period=DashboardPeriod(
            period_type=period_type, start=period_start, end=period_end
        ),
        scorecards=scorecards,
        trends={"kpi_percentage": kpi_trend},
        kpi_status_distribution=kpi_status_distribution,
        department_comparison=comparisons,
        task_diagnostics=task_diagnostics,
        approval_diagnostics=approval_diagnostics,
        category_performance=CategoryPerformance(
            status="unavailable",
            reason="category_results_not_independently_stored",
        ),
        alerts=alerts,
        activity=activity,
        activity_summary=activity_summary,
        notifications=DashboardNotifications(
            unread_count=unread_count,
            recent=[
                {
                    "id": item.id,
                    "title": item.title,
                    "message": item.message,
                    "notification_type": item.notification_type,
                    "created_at": item.created_at,
                }
                for item in notifications
            ],
        ),
        data_quality=_dashboard_data_quality(
            kpi_row, task_diagnostics, kpi_trend, comparisons
        ),
    )
