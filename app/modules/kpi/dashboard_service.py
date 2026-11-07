import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, func, literal, literal_column, or_, select
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
from app.modules.kpi.parallel import gather_on_sessions
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


def _finalized_kpi_records_cte(
    period_type: PeriodType,
    period_start: datetime | None,
    period_end: datetime | None,
):
    """The same finalized-record set as ``_finalized_kpi_records``, as a CTE.

    The three summary aggregates (overall / trend / status) share this
    exact base, so one CTE referenced 3x lets the DB scan the dedup
    window once instead of 3x. A CTE referenced more than once is
    materialized by default on both PG and SQLite; if a future planner
    inlines it instead, the results are identical — only the saving
    vanishes, so the failure mode is safe.
    """
    records = _deduplicated_kpi_records()
    statement = select(records).where(records.c.record_rank == 1)
    statement = _period_filters(
        statement,
        records.c.period_type,
        records.c.period_start,
        records.c.period_end,
        period_type,
        period_start,
        period_end,
    )
    return statement.cte("finalized_kpi_records")


def _kpi_aggregates_statement(
    user_ids: list[UUID],
    period_type: PeriodType,
    period_start: datetime | None,
    period_end: datetime | None,
):
    """Overall + trend + status distribution as ONE UNION ALL statement.

    The three branches share the finalized-records CTE and the same
    user filter; they differ only in their GROUP BY. ``kind_order``
    keeps the branches in overall/trend/status order, and the trailing
    keys restore each branch's old ORDER BY (trend by period_start,
    status by status_bucket) — the overall branch always yields one
    row, so its keys never matter.
    """
    finalized = _finalized_kpi_records_cte(period_type, period_start, period_end)
    user_filter = (
        finalized.c.user_id.in_(user_ids)
        if user_ids
        else finalized.c.user_id == UUID(int=0)
    )
    # The UNION's result column types come from the FIRST branch, so
    # its NULLs must carry the real column types — a plain NULL would
    # leave period_start untyped and SQLite would hand it back as a
    # string (the trend shaper calls .isoformat() on it).
    overall = select(
        literal("overall").label("kind"),
        literal(0).label("kind_order"),
        literal(None, KPIRecord.period_start.type).label("period_start"),
        literal(None, KPIRecord.status_bucket.type).label("status_bucket"),
        func.avg(finalized.c.weighted_score).label("score"),
        func.avg(finalized.c.percentage).label("percentage"),
        func.count(finalized.c.id).label("count"),
    ).where(user_filter)
    trend = (
        select(
            literal("trend").label("kind"),
            literal(1).label("kind_order"),
            finalized.c.period_start.label("period_start"),
            literal(None).label("status_bucket"),
            literal(None).label("score"),
            func.avg(finalized.c.percentage).label("percentage"),
            func.count(finalized.c.id).label("count"),
        )
        .where(user_filter)
        .group_by(finalized.c.period_start)
    )
    status = (
        select(
            literal("status").label("kind"),
            literal(2).label("kind_order"),
            literal(None).label("period_start"),
            finalized.c.status_bucket.label("status_bucket"),
            literal(None).label("score"),
            literal(None).label("percentage"),
            func.count(finalized.c.id).label("count"),
        )
        .where(user_filter)
        .group_by(finalized.c.status_bucket)
    )
    union = overall.union_all(trend, status)
    # CompoundSelect has no .c in SQLAlchemy 2.1, so the ORDER BY
    # names the output columns directly (valid on both PG and SQLite).
    return union.order_by(
        literal_column("kind_order"),
        literal_column("period_start"),
        literal_column("status_bucket"),
    )


def _trend_from_rows(rows) -> DashboardTrend:
    """Shape trend rows into DashboardTrend (was _build_kpi_trend's body)."""
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


def _status_distribution_from_rows(rows) -> list[KPIStatusCount]:
    """Shape status rows into KPIStatusCount list (was _build_kpi_status_distribution's body)."""
    return [
        KPIStatusCount(
            status=getattr(row.status_bucket, "value", str(row.status_bucket)),
            count=int(row.count or 0),
        )
        for row in rows
    ]


async def _build_kpi_aggregates(
    db: AsyncSession,
    user_ids: list[UUID],
    period_type: PeriodType,
    period_start: datetime | None,
    period_end: datetime | None,
):
    """Overall row + trend + status distribution in ONE statement (F5).

    Returns the same three values the old _build_kpi_overall /
    _build_kpi_trend / _build_kpi_status_distribution returned, from
    one UNION ALL over the shared finalized-records CTE — 3 statements
    become 1 and the dedup window is scanned once instead of 3x.
    The overall row keeps its .score/.percentage/.count attributes, so
    callers read it exactly as before.
    """
    rows = (
        await db.execute(
            _kpi_aggregates_statement(
                user_ids, period_type, period_start, period_end
            )
        )
    ).all()
    overall_row = next(row for row in rows if row.kind == "overall")
    trend_rows = [row for row in rows if row.kind == "trend"]
    status_rows = [row for row in rows if row.kind == "status"]
    return (
        overall_row,
        _trend_from_rows(trend_rows),
        _status_distribution_from_rows(status_rows),
    )


async def _build_department_comparisons(
    db: AsyncSession,
    user_ids: list[UUID],
    department_ids: list[UUID] | None,
    period_type: PeriodType,
    period_start: datetime | None,
    period_end: datetime | None,
) -> list[DepartmentComparison]:
    """Department comparison rows. Runs after the scope gate because it
    needs the gate's comparison department list; the query itself is the
    same statement the old combined helper ran."""
    records = _finalized_kpi_records(period_type, period_start, period_end)
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
    return comparisons


def _comparison_status(row_count: int) -> str:
    if row_count > 1:
        return "available"
    if row_count == 1:
        return "insufficient_data"
    return "unavailable"


async def _activity_filters(
    db: AsyncSession,
    current_user: User,
    department_user_ids: list[UUID] | None = None,
):
    """Activity where-clause values. The scope lookup is the only part
    that needs a query before the three independent activity queries can
    run; the returned filters are plain values, usable on any session."""
    filters = [ActivityTimeline.is_deleted.is_(False)]
    if department_user_ids is not None:
        filters.append(ActivityTimeline.user_id.in_(department_user_ids))
    else:
        actor_ids = await get_activity_scope_user_ids(db, current_user)
        if actor_ids is not None:
            filters.append(ActivityTimeline.user_id.in_(actor_ids))
    return filters


async def _build_activity_data(
    db: AsyncSession,
    current_user: User,
    department_user_ids: list[UUID] | None = None,
) -> tuple[list[dict], ActivitySummary]:
    filters = await _activity_filters(db, current_user, department_user_ids)
    recent_statement = (
        select(ActivityTimeline)
        .options(joinedload(ActivityTimeline.user))
        .where(*filters)
        .order_by(ActivityTimeline.created_at.desc())
        .limit(10)
    )

    async def _recent(session: AsyncSession):
        return (await session.execute(recent_statement)).scalars().all()

    async def _total(session: AsyncSession):
        return int(
            (
                await session.execute(
                    select(func.count(ActivityTimeline.id)).where(*filters)
                )
            ).scalar_one()
            or 0
        )

    async def _by_action(session: AsyncSession):
        return (
            await session.execute(
                select(
                    ActivityTimeline.action,
                    func.count(ActivityTimeline.id).label("count"),
                )
                .where(*filters)
                .group_by(ActivityTimeline.action)
                .order_by(
                    func.count(ActivityTimeline.id).desc(),
                    ActivityTimeline.action,
                )
                .limit(10)
            )
        ).all()

    activities, total, action_rows = await gather_on_sessions(
        db, [_recent, _total, _by_action]
    )
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

    # Parallel batch 1: every query that does not depend on another's
    # result. The scope gate runs here too, but the department
    # comparisons need its output, so they wait for batch 2. The
    # activity helper fans out to its own three queries internally.
    # Everything below assembles the same values in the same order as
    # the old sequential code; only execution order changed.
    async def _scope(session: AsyncSession):
        return await _dashboard_scope(session, current_user, department_ids)

    async def _kpi_agg(session: AsyncSession):
        # F5: overall + trend + status distribution in one statement
        # over the shared finalized-records CTE (was 3 statements).
        return await _build_kpi_aggregates(
            session, scope_user_ids, period_type, period_start, period_end
        )

    async def _task_diag(session: AsyncSession):
        return await _build_task_diagnostics(session, scope_user_ids)

    async def _dept_count(session: AsyncSession):
        return int(
            (
                await session.execute(
                    select(func.count(Department.id)).where(
                        Department.is_deleted.is_(False)
                    )
                )
            ).scalar_one()
        )

    async def _activity(session: AsyncSession):
        return await _build_activity_data(
            session,
            current_user,
            scope_user_ids if department_filtered else None,
        )

    async def _notif_recent(session: AsyncSession):
        return await get_notifications_by_user(
            session, current_user.id, limit=5
        )

    async def _notif_unread(session: AsyncSession):
        if department_filtered:
            notification_filters = [
                Notification.is_deleted.is_(False),
                Notification.user_id.in_(scope_user_ids),
            ]
            return int(
                (
                    await session.execute(
                        select(func.count(Notification.id)).where(
                            *notification_filters,
                            Notification.is_read.is_(False),
                        )
                    )
                ).scalar_one()
                or 0
            )
        return await count_unread_notifications(session, current_user.id)

    batch_jobs = [
        _scope,
        _kpi_agg,
        _task_diag,
        _activity,
        _notif_recent,
        _notif_unread,
    ]
    if department_ids is None:
        batch_jobs.append(_dept_count)
    batch = await gather_on_sessions(db, batch_jobs)
    (
        role_name,
        level,
        visible_departments,
        comparison_department_ids,
    ) = batch[0]
    kpi_row, kpi_trend, kpi_status_distribution = batch[1]
    task_diagnostics, approval_diagnostics = batch[2]
    activity, activity_summary = batch[3]
    notifications = batch[4]
    unread_count = batch[5]
    department_count = (
        batch[6] if department_ids is None else len(visible_departments)
    )

    # Parallel batch 2: department comparisons, the one piece that
    # genuinely depends on the scope gate's output. The request session
    # is idle now (batch 1 sessions are closed), so it runs here.
    comparisons = await _build_department_comparisons(
        db,
        scope_user_ids,
        comparison_department_ids,
        period_type,
        period_start,
        period_end,
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
