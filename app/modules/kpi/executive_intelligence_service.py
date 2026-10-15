from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.departments.models import Department, UserDepartment
from app.modules.kpi.executive_intelligence_schemas import (
    ExecutiveAttentionArea,
    ExecutiveDepartment,
    ExecutiveEmployeeDetail,
    ExecutiveEmployeeHistoryPoint,
    ExecutiveEmployeePage,
    ExecutiveEmployeeResult,
    ExecutiveOverview,
    ExecutivePeriodOption,
    ExecutiveSignal,
    ExecutiveStatusCount,
    ExecutiveSummary,
    ExecutiveTrend,
    ExecutiveTrendPoint,
    ExecutiveUnavailableData,
)
from app.modules.kpi.models import (
    KPIFormula,
    KPIHistory,
    KPIPeriod,
    KPIPeriodStatus,
    KPIRecord,
    KPIStatus,
    PeriodType,
)
from app.modules.users.models import Position, User

STATUS_ORDER = (
    KPIStatus.EXCELLENT,
    KPIStatus.VERY_GOOD,
    KPIStatus.GOOD,
    KPIStatus.FAIR,
    KPIStatus.POOR,
)
FINAL_PERIOD_STATUSES = (KPIPeriodStatus.FINALIZED, KPIPeriodStatus.LOCKED)
TREND_PERIOD_LIMIT = 12


def _round_metric(value: float | None) -> float | None:
    return round(float(value), 2) if value is not None else None


def _trend_change(current: float, previous: float) -> tuple[float, str]:
    difference = current - previous
    if difference > 0:
        direction = "UP"
    elif difference < 0:
        direction = "DOWN"
    else:
        direction = "FLAT"
    return round(difference, 2), direction


def _user_filters(user_ids: list[UUID] | None):
    if user_ids is None:
        return []
    return [User.id.in_(user_ids)] if user_ids else [User.id == UUID(int=0)]


def _record_filters(records, user_ids: list[UUID] | None, department_ids: list[UUID] | None):
    filters = [records.c.record_rank == 1]
    if user_ids is not None:
        filters.append(
            records.c.user_id.in_(user_ids)
            if user_ids
            else records.c.user_id == UUID(int=0)
        )
    if department_ids is not None:
        filters.append(
            records.c.department_id.in_(department_ids)
            if department_ids
            else records.c.department_id == UUID(int=0)
        )
    return filters


def _ranked_records(
    period_ids: list[UUID] | None = None,
    period_type: PeriodType | None = None,
):
    rank = func.row_number().over(
        partition_by=[KPIRecord.kpi_period_id, KPIRecord.user_id],
        order_by=[
            KPIRecord.updated_at.desc().nulls_last(),
            KPIRecord.created_at.desc(),
            KPIRecord.id.desc(),
        ],
    ).label("record_rank")
    statement = (
        select(
            KPIRecord.id,
            KPIRecord.kpi_period_id,
            KPIRecord.user_id,
            KPIRecord.department_id,
            KPIRecord.percentage,
            KPIRecord.weighted_score,
            KPIRecord.raw_score,
            KPIRecord.status_bucket,
            KPIRecord.formula_id,
            rank,
        )
        .join(KPIPeriod, KPIPeriod.id == KPIRecord.kpi_period_id)
        .join(User, User.id == KPIRecord.user_id)
        .outerjoin(Department, Department.id == KPIRecord.department_id)
        .where(
            KPIRecord.is_deleted.is_(False),
            KPIPeriod.is_deleted.is_(False),
            KPIPeriod.status.in_(FINAL_PERIOD_STATUSES),
            User.is_deleted.is_(False),
            User.is_active.is_(True),
            or_(KPIRecord.department_id.is_(None), Department.is_deleted.is_(False)),
        )
    )
    if period_ids is not None:
        statement = statement.where(KPIRecord.kpi_period_id.in_(period_ids))
    if period_type is not None:
        statement = statement.where(KPIRecord.period_type == period_type)
    return statement.subquery()


def _status_columns(records):
    return [
        func.count(records.c.id)
        .filter(records.c.status_bucket == status)
        .label(f"status_{status.value.lower()}")
        for status in STATUS_ORDER
    ]


def _status_distribution(row, evaluated_count: int) -> list[ExecutiveStatusCount]:
    result = []
    for status in STATUS_ORDER:
        count = int(getattr(row, f"status_{status.value.lower()}", 0) or 0)
        result.append(
            ExecutiveStatusCount(
                status=status,
                count=count,
                percentage=(
                    _round_metric(count / evaluated_count * 100)
                    if evaluated_count
                    else None
                ),
            )
        )
    return result


async def _load_periods(
    db: AsyncSession,
    scope_user_ids: list[UUID] | None,
    department_ids: list[UUID] | None,
) -> list[KPIPeriod]:
    statement = select(KPIPeriod).where(
        KPIPeriod.is_deleted.is_(False),
        KPIPeriod.status.in_(FINAL_PERIOD_STATUSES),
    )
    if scope_user_ids is not None or department_ids is not None:
        records = _ranked_records()
        authorized_period_ids = (
            select(records.c.kpi_period_id)
            .where(*_record_filters(records, scope_user_ids, department_ids))
            .distinct()
            .subquery()
        )
        statement = statement.join(
            authorized_period_ids,
            authorized_period_ids.c.kpi_period_id == KPIPeriod.id,
        )

    return list(
        (
            await db.execute(
                statement.order_by(KPIPeriod.start_date.desc(), KPIPeriod.id.desc())
            )
        )
        .scalars()
        .all()
    )


def _default_period(periods: list[KPIPeriod]) -> KPIPeriod | None:
    for preferred_status in (
        KPIPeriodStatus.OPEN,
        KPIPeriodStatus.IN_PROGRESS,
    ):
        match = next(
            (period for period in periods if period.status == preferred_status),
            None,
        )
        if match:
            return match
    finalized_or_locked = next(
        (period for period in periods if period.status in FINAL_PERIOD_STATUSES),
        None,
    )
    if finalized_or_locked:
        return finalized_or_locked
    return periods[0] if periods else None


def _period_option(
    period: KPIPeriod, period_type: PeriodType | None = None
) -> ExecutivePeriodOption:
    return ExecutivePeriodOption(
        id=period.id,
        label=period.label,
        period_type=period_type,
        start_date=period.start_date,
        end_date=period.end_date,
        status=period.status,
    )


async def _load_period_types(
    db: AsyncSession,
    periods: list[KPIPeriod],
    scope_user_ids: list[UUID] | None,
    department_ids: list[UUID] | None,
) -> dict[UUID, set[PeriodType]]:
    if not periods:
        return {}
    records = _ranked_records([period.id for period in periods])
    rows = (
        await db.execute(
            select(records.c.kpi_period_id, KPIRecord.period_type)
            .join(KPIRecord, KPIRecord.id == records.c.id)
            .where(
                *_record_filters(records, scope_user_ids, department_ids),
            )
            .distinct()
        )
    ).all()
    result: dict[UUID, set[PeriodType]] = {}
    for row in rows:
        result.setdefault(row.kpi_period_id, set()).add(row.period_type)
    return result


async def _load_active_employee_count(
    db: AsyncSession, scope_user_ids: list[UUID] | None
) -> int:
    return int(
        (
            await db.execute(
                select(func.count(User.id)).where(
                    User.is_deleted.is_(False),
                    User.is_active.is_(True),
                    *_user_filters(scope_user_ids),
                )
            )
        ).scalar_one()
        or 0
    )


async def _load_period_aggregate(
    db: AsyncSession,
    period_id: UUID,
    scope_user_ids: list[UUID] | None,
    department_ids: list[UUID] | None,
    period_type: PeriodType | None,
):
    records = _ranked_records([period_id], period_type)
    row = (
        await db.execute(
            select(
                func.avg(records.c.percentage).label("average_percentage"),
                func.count(records.c.id).label("evaluated_count"),
                *_status_columns(records),
            ).where(*_record_filters(records, scope_user_ids, department_ids))
        )
    ).one()
    return row


async def _load_department_metrics(
    db: AsyncSession,
    period_id: UUID,
    scope_user_ids: list[UUID] | None,
    department_ids: list[UUID] | None,
) -> list[ExecutiveDepartment]:
    if department_ids == []:
        return []
    records = _ranked_records([period_id])
    statement = (
        select(
            Department.id.label("department_id"),
            Department.name.label("department_name"),
            func.count(func.distinct(UserDepartment.user_id)).label("employee_count"),
            func.count(func.distinct(records.c.user_id)).label("evaluated_count"),
            func.avg(records.c.percentage).label("average_percentage"),
            *_status_columns(records),
        )
        .join(
            UserDepartment,
            and_(
                UserDepartment.department_id == Department.id,
                UserDepartment.is_deleted.is_(False),
            ),
        )
        .join(
            User,
            and_(
                User.id == UserDepartment.user_id,
                User.is_deleted.is_(False),
                User.is_active.is_(True),
            ),
        )
        .outerjoin(
            records,
            and_(
                records.c.user_id == UserDepartment.user_id,
                records.c.department_id == Department.id,
                records.c.record_rank == 1,
            ),
        )
        .where(Department.is_deleted.is_(False), *_user_filters(scope_user_ids))
        .group_by(Department.id, Department.name)
        .order_by(Department.name, Department.id)
    )
    if department_ids is not None:
        statement = statement.where(Department.id.in_(department_ids))
    rows = (await db.execute(statement)).all()
    result = []
    for row in rows:
        evaluated_count = int(row.evaluated_count or 0)
        employee_count = int(row.employee_count or 0)
        counts = {
            status: int(getattr(row, f"status_{status.value.lower()}", 0) or 0)
            for status in STATUS_ORDER
        }
        below_fair_count = counts[KPIStatus.POOR]
        at_or_above_good_count = (
            counts[KPIStatus.EXCELLENT]
            + counts[KPIStatus.VERY_GOOD]
            + counts[KPIStatus.GOOD]
        )
        result.append(
            ExecutiveDepartment(
                department_id=row.department_id,
                department_name=row.department_name,
                employee_count=employee_count,
                evaluated_employee_count=evaluated_count,
                no_result_employee_count=max(employee_count - evaluated_count, 0),
                average_percentage=_round_metric(row.average_percentage),
                excellent_count=counts[KPIStatus.EXCELLENT],
                very_good_count=counts[KPIStatus.VERY_GOOD],
                good_count=counts[KPIStatus.GOOD],
                fair_count=counts[KPIStatus.FAIR],
                poor_count=counts[KPIStatus.POOR],
                below_fair_employee_count=below_fair_count,
                below_fair_percentage=(
                    _round_metric(below_fair_count / evaluated_count * 100)
                    if evaluated_count
                    else None
                ),
                at_or_above_good_employee_count=at_or_above_good_count,
                at_or_above_good_percentage=(
                    _round_metric(at_or_above_good_count / evaluated_count * 100)
                    if evaluated_count
                    else None
                ),
            )
        )
    return result


async def _load_trend(
    db: AsyncSession,
    selected_period: KPIPeriod,
    selected_period_type: PeriodType | None,
    scope_user_ids: list[UUID] | None,
    department_ids: list[UUID] | None,
) -> ExecutiveTrend:
    if selected_period_type is None:
        return _unavailable_trend()
    periods = await _matching_trend_periods(
        db, selected_period, selected_period_type
    )
    period_ids = [period.id for period in periods]
    aggregate_rows = await _trend_aggregate_rows(
        db, period_ids, selected_period_type, scope_user_ids, department_ids
    )
    points = _trend_points(periods, aggregate_rows)
    delta, direction = _trend_comparison(
        points, selected_period, aggregate_rows
    )
    return ExecutiveTrend(
        points=points,
        delta=delta,
        direction=direction,
        comparison_available=direction != "UNAVAILABLE",
    )


async def _matching_trend_periods(
    db: AsyncSession,
    selected_period: KPIPeriod,
    selected_period_type: PeriodType,
) -> list[KPIPeriod]:
    period_ids_with_matching_type = (
        select(KPIRecord.kpi_period_id)
        .join(User, User.id == KPIRecord.user_id)
        .outerjoin(Department, Department.id == KPIRecord.department_id)
        .where(
            KPIRecord.period_type == selected_period_type,
            KPIRecord.is_deleted.is_(False),
            KPIRecord.kpi_period_id.is_not(None),
            User.is_deleted.is_(False),
            User.is_active.is_(True),
            or_(
                KPIRecord.department_id.is_(None),
                Department.is_deleted.is_(False),
            ),
        )
        .distinct()
    )
    selected_period_order = or_(
        KPIPeriod.start_date < selected_period.start_date,
        and_(
            KPIPeriod.start_date == selected_period.start_date,
            KPIPeriod.id <= selected_period.id,
        ),
    )
    periods = list(
        (
            await db.execute(
                select(KPIPeriod)
                .where(
                    KPIPeriod.is_deleted.is_(False),
                    KPIPeriod.status.in_(FINAL_PERIOD_STATUSES),
                    selected_period_order,
                    (
                        KPIPeriod.id.in_(period_ids_with_matching_type)
                        | (KPIPeriod.id == selected_period.id)
                    ),
                )
                .order_by(KPIPeriod.start_date.desc(), KPIPeriod.id.desc())
                .limit(TREND_PERIOD_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    periods.reverse()
    return periods


async def _trend_aggregate_rows(
    db: AsyncSession,
    period_ids: list[UUID],
    selected_period_type: PeriodType,
    scope_user_ids: list[UUID] | None,
    department_ids: list[UUID] | None,
) -> dict:
    if not period_ids:
        return {}
    records = _ranked_records(period_ids, selected_period_type)
    rows = (
        await db.execute(
            select(
                records.c.kpi_period_id,
                func.avg(records.c.percentage).label("average_percentage"),
                func.count(records.c.id).label("evaluated_count"),
                *_status_columns(records),
            )
            .where(*_record_filters(records, scope_user_ids, department_ids))
            .group_by(records.c.kpi_period_id)
        )
    ).all()
    return {row.kpi_period_id: row for row in rows}


def _trend_points(periods: list[KPIPeriod], aggregate_rows: dict) -> list[ExecutiveTrendPoint]:
    points = []
    for period in periods:
        points.append(_trend_point(period, aggregate_rows.get(period.id)))
    return points


def _trend_point(
    period: KPIPeriod, row
) -> ExecutiveTrendPoint:
    evaluated_count = int(row.evaluated_count or 0) if row else 0
    return ExecutiveTrendPoint(
        period_id=period.id,
        period_label=period.label,
        average_percentage=(
            _round_metric(row.average_percentage) if row else None
        ),
        evaluated_employee_count=evaluated_count,
        status_distribution=(
            _status_distribution(row, evaluated_count)
            if row
            else [
                ExecutiveStatusCount(status=status, count=0, percentage=None)
                for status in STATUS_ORDER
            ]
        ),
    )


def _trend_comparison(
    points: list[ExecutiveTrendPoint],
    selected_period: KPIPeriod,
    aggregate_rows: dict,
) -> tuple[float | None, str]:
    selected_point = next(
        (point for point in points if point.period_id == selected_period.id), None
    )
    previous_point = (
        points[-2]
        if selected_point is not None
        and points
        and points[-1].period_id == selected_period.id
        and len(points) > 1
        else None
    )
    delta = None
    direction = "UNAVAILABLE"
    if (
        selected_point
        and previous_point
        and selected_point.average_percentage is not None
        and previous_point.average_percentage is not None
    ):
        raw_current = aggregate_rows[selected_period.id].average_percentage
        raw_previous = aggregate_rows[previous_point.period_id].average_percentage
        delta, direction = _trend_change(
            float(raw_current), float(raw_previous)
        )
    return delta, direction


def _build_signals(
    period: KPIPeriod,
    summary: ExecutiveSummary,
    trend: ExecutiveTrend,
) -> tuple[list[ExecutiveSignal], list[ExecutiveAttentionArea]]:
    signals = []
    attention = []
    if summary.status != "available":
        return signals, attention

    if trend.comparison_available:
        signal_type = {
            "UP": "IMPROVEMENT",
            "DOWN": "DECLINE",
            "FLAT": "STABILITY",
        }[trend.direction]
        description = {
            "UP": "The current period average is higher than the previous comparable period.",
            "DOWN": "The current period average is lower than the previous comparable period.",
            "FLAT": "The current and previous comparable period averages are unchanged.",
        }[trend.direction]
        signals.append(
            ExecutiveSignal(
                signal_type=signal_type,
                severity="info",
                title=f"KPI average {trend.direction.lower()}",
                explanation=f"{description} Change: {trend.delta:.2f} percentage points.",
                metric_name="period_over_period_delta",
                metric_value=trend.delta or 0,
                period_id=period.id,
                period_label=period.label,
            )
        )

    evaluated = summary.evaluated_employee_count or 0
    below_fair = sum(
        item.count
        for item in summary.status_distribution
        if item.status in {KPIStatus.FAIR, KPIStatus.POOR}
    )
    below_fair_percentage = (
        _round_metric(below_fair / evaluated * 100) if evaluated else None
    )
    if below_fair and below_fair_percentage is not None:
        signals.append(
            ExecutiveSignal(
                signal_type="CONCENTRATION",
                severity="info",
                title="FAIR/POOR results are present",
                explanation=(
                    f"{below_fair} of {evaluated} evaluated employees "
                    f"({below_fair_percentage:.2f}%) have a FAIR or POOR result."
                ),
                metric_name="fair_or_poor_employee_percentage",
                metric_value=below_fair_percentage,
                period_id=period.id,
                period_label=period.label,
            )
        )
        attention.append(
            ExecutiveAttentionArea(
                area_type="STATUS_CONCENTRATION",
                severity="info",
                title="FAIR/POOR KPI results require review",
                explanation=(
                    f"{below_fair} of {evaluated} evaluated employees "
                    f"({below_fair_percentage:.2f}%) are in FAIR or POOR status."
                ),
                metric_name="fair_or_poor_employee_count",
                metric_value=float(below_fair),
                period_id=period.id,
                period_label=period.label,
            )
        )

    if summary.no_result_employee_count:
        missing = summary.no_result_employee_count
        active_count = summary.active_employee_count or 0
        missing_percentage = (
            _round_metric(missing / active_count * 100) if active_count else 0.0
        )
        signals.append(
            ExecutiveSignal(
                signal_type="COVERAGE",
                severity="info",
                title="KPI results are not available for all active employees",
                explanation=(
                    f"{missing} of {active_count} active employees "
                    f"({missing_percentage:.2f}%) have no finalized result."
                ),
                metric_name="no_result_employee_count",
                metric_value=float(missing),
                period_id=period.id,
                period_label=period.label,
            )
        )
        attention.append(
            ExecutiveAttentionArea(
                area_type="MISSING_RESULTS",
                severity="info",
                title="Some active employees have no finalized KPI result",
                explanation=(
                    f"{missing} of {active_count} active employees have no "
                    f"finalized result for this period."
                ),
                metric_name="no_result_employee_count",
                metric_value=float(missing),
                period_id=period.id,
                period_label=period.label,
            )
        )
    return signals, attention


def _unavailable_trend() -> ExecutiveTrend:
    return ExecutiveTrend(
        points=[],
        delta=None,
        direction="UNAVAILABLE",
        comparison_available=False,
    )


def _empty_executive_overview(summary: ExecutiveSummary) -> ExecutiveOverview:
    return ExecutiveOverview(
        periods=[],
        selected_period=None,
        summary=summary,
        trend=_unavailable_trend(),
        category_intelligence=ExecutiveUnavailableData(
            reason="Finalized category results are not independently persisted."
        ),
        target_intelligence=ExecutiveUnavailableData(
            reason="Authoritative target-versus-actual results are not independently persisted."
        ),
    )


def _selected_executive_period(
    periods: list[KPIPeriod], period_id: UUID | None
) -> KPIPeriod | None:
    if period_id is not None:
        selected_period = next(
            (period for period in periods if period.id == period_id), None
        )
        if selected_period is None:
            raise LookupError("KPI period not found.")
        return selected_period
    return _default_period(periods)


async def _finalized_overview_data(
    db: AsyncSession,
    selected_period: KPIPeriod,
    selected_period_type: PeriodType | None,
    selected_types: set[PeriodType],
    active_count: int,
    empty_summary: ExecutiveSummary,
    scope_user_ids: list[UUID] | None,
    department_ids: list[UUID] | None,
    show_departments: bool,
):
    if len(selected_types) > 1:
        return empty_summary, [], _unavailable_trend(), [], []

    aggregate = await _load_period_aggregate(
        db,
        selected_period.id,
        scope_user_ids,
        department_ids,
        selected_period_type,
    )
    evaluated_count = int(aggregate.evaluated_count or 0)
    summary = ExecutiveSummary(
        status="available",
        average_percentage=_round_metric(aggregate.average_percentage),
        evaluated_employee_count=evaluated_count,
        no_result_employee_count=max(active_count - evaluated_count, 0),
        active_employee_count=active_count,
        status_distribution=_status_distribution(aggregate, evaluated_count),
    )
    departments = (
        await _load_department_metrics(
            db, selected_period.id, scope_user_ids, department_ids
        )
        if show_departments
        else []
    )
    trend = await _load_trend(
        db,
        selected_period,
        selected_period_type,
        scope_user_ids,
        department_ids,
    )
    signals, attention = _build_signals(selected_period, summary, trend)
    attention.extend(_department_attention_areas(selected_period, departments))
    return summary, departments, trend, signals, attention


def _department_attention_areas(
    period: KPIPeriod, departments: list[ExecutiveDepartment]
) -> list[ExecutiveAttentionArea]:
    attention = []
    for department in departments:
        fair_or_poor_count = department.fair_count + department.poor_count
        if not fair_or_poor_count:
            continue
        fair_or_poor_percentage = _round_metric(
            fair_or_poor_count / department.evaluated_employee_count * 100
        )
        attention.append(
            ExecutiveAttentionArea(
                area_type="STATUS_CONCENTRATION",
                severity="info",
                title=f"FAIR/POOR results are present in {department.department_name}",
                explanation=(
                    f"{fair_or_poor_count} of "
                    f"{department.evaluated_employee_count} evaluated "
                    f"employees ({fair_or_poor_percentage:.2f}%) "
                    "have a FAIR or POOR result."
                ),
                metric_name="fair_or_poor_employee_percentage",
                metric_value=fair_or_poor_percentage or 0,
                period_id=period.id,
                period_label=period.label,
                department_id=department.department_id,
            )
        )
    return attention


async def list_executive_employee_results(
    db: AsyncSession,
    *,
    period_id: UUID,
    scope_user_ids: list[UUID] | None,
    department_ids: list[UUID] | None,
    offset: int,
    limit: int,
) -> ExecutiveEmployeePage:
    period = await db.scalar(
        select(KPIPeriod).where(
            KPIPeriod.id == period_id,
            KPIPeriod.is_deleted.is_(False),
            KPIPeriod.status.in_(FINAL_PERIOD_STATUSES),
        )
    )
    if period is None:
        return ExecutiveEmployeePage(items=[], total=0, offset=offset, limit=limit)
    period_types = await _load_period_types(
        db, [period], scope_user_ids, department_ids
    )
    period_type_values = period_types.get(period.id, set())
    if len(period_type_values) > 1:
        return ExecutiveEmployeePage(items=[], total=0, offset=offset, limit=limit)
    period_type = next(iter(period_type_values)) if period_type_values else None
    if period_type is None:
        return ExecutiveEmployeePage(items=[], total=0, offset=offset, limit=limit)

    records = _ranked_records([period_id], period_type)
    filters = _record_filters(records, scope_user_ids, department_ids)
    total = int(
        (
            await db.execute(
                select(func.count(records.c.id)).where(*filters)
            )
        ).scalar_one()
        or 0
    )
    rows = (
        await db.execute(
            select(
                records.c.user_id,
                records.c.percentage,
                records.c.status_bucket,
                User.first_name,
                User.last_name,
                Position.name.label("position_name"),
                Department.name.label("department_name"),
                UserDepartment.user_level,
            )
            .join(User, User.id == records.c.user_id)
            .outerjoin(Position, Position.id == User.position_id)
            .outerjoin(Department, Department.id == records.c.department_id)
            .outerjoin(
                UserDepartment,
                and_(
                    UserDepartment.user_id == User.id,
                    UserDepartment.department_id == records.c.department_id,
                    UserDepartment.is_deleted.is_(False),
                ),
            )
            .where(*filters)
            .order_by(User.first_name, User.last_name, User.id)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return ExecutiveEmployeePage(
        items=[
            ExecutiveEmployeeResult(
                user_id=row.user_id,
                employee_name=f"{row.first_name} {row.last_name}".strip(),
                department_name=row.department_name,
                position_name=row.position_name,
                level=row.user_level,
                percentage=round(float(row.percentage), 2),
                status=row.status_bucket,
            )
            for row in rows
        ],
        total=total,
        offset=offset,
        limit=limit,
    )


async def build_executive_overview(
    db: AsyncSession,
    *,
    scope_user_ids: list[UUID] | None,
    department_ids: list[UUID] | None,
    show_departments: bool,
    period_id: UUID | None = None,
) -> ExecutiveOverview:
    periods = await _load_periods(db, scope_user_ids, department_ids)
    period_types = await _load_period_types(
        db, periods, scope_user_ids, department_ids
    )
    selected_period = _selected_executive_period(periods, period_id)
    active_count = await _load_active_employee_count(db, scope_user_ids)
    empty_summary = ExecutiveSummary(
        status="unavailable",
        average_percentage=None,
        evaluated_employee_count=None,
        no_result_employee_count=None,
        active_employee_count=None,
    )
    if selected_period is None:
        return _empty_executive_overview(empty_summary)

    selected_types = period_types.get(selected_period.id, set())
    selected_period_type = next(iter(selected_types)) if len(selected_types) == 1 else None
    if selected_period.status in FINAL_PERIOD_STATUSES:
        (
            summary,
            departments,
            trend,
            signals,
            attention,
        ) = await _finalized_overview_data(
            db,
            selected_period,
            selected_period_type,
            selected_types,
            active_count,
            empty_summary,
            scope_user_ids,
            department_ids,
            show_departments,
        )
    else:
        summary = empty_summary
        departments = []
        trend = _unavailable_trend()
        signals, attention = [], []

    return ExecutiveOverview(
        periods=[
            _period_option(
                period,
                next(iter(period_types[period.id]))
                if len(period_types.get(period.id, set())) == 1
                else None,
            )
            for period in periods
        ],
        selected_period=_period_option(selected_period, selected_period_type),
        summary=summary,
        departments=departments,
        trend=trend,
        signals=signals,
        attention_areas=attention,
        category_intelligence=ExecutiveUnavailableData(
            reason="Finalized category results are not independently persisted."
        ),
        target_intelligence=ExecutiveUnavailableData(
            reason="Authoritative target-versus-actual results are not independently persisted."
        ),
    )


async def get_executive_employee_detail(
    db: AsyncSession,
    *,
    user_id: UUID,
    period_id: UUID,
    scope_user_ids: list[UUID] | None,
    department_ids: list[UUID] | None,
) -> ExecutiveEmployeeDetail | None:
    period = await db.scalar(
        select(KPIPeriod).where(
            KPIPeriod.id == period_id,
            KPIPeriod.is_deleted.is_(False),
            KPIPeriod.status.in_(FINAL_PERIOD_STATUSES),
        )
    )
    if period is None:
        return None
    period_types = await _load_period_types(
        db, [period], scope_user_ids, department_ids
    )
    matching_types = period_types.get(period_id, set())
    if len(matching_types) != 1:
        return None
    records = _ranked_records([period_id], next(iter(matching_types)))
    query = (
        select(
            records.c.id.label("record_id"),
            records.c.raw_score,
            records.c.weighted_score,
            records.c.percentage,
            records.c.status_bucket,
            User.first_name,
            User.last_name,
            Position.name.label("position_name"),
            Department.name.label("department_name"),
            KPIFormula.name.label("formula_name"),
            UserDepartment.user_level,
        )
        .join(User, User.id == records.c.user_id)
        .outerjoin(Position, Position.id == User.position_id)
        .outerjoin(Department, Department.id == records.c.department_id)
        .outerjoin(
            UserDepartment,
            and_(
                UserDepartment.user_id == User.id,
                UserDepartment.department_id == records.c.department_id,
                UserDepartment.is_deleted.is_(False),
            ),
        )
        .outerjoin(KPIFormula, KPIFormula.id == records.c.formula_id)
        .where(
            records.c.user_id == user_id,
            User.is_deleted.is_(False),
            User.is_active.is_(True),
            or_(
                records.c.department_id.is_(None),
                Department.is_deleted.is_(False),
            ),
            *_record_filters(records, scope_user_ids, department_ids),
        )
        .order_by(
            records.c.record_rank,
        )
        .limit(1)
    )
    row = (await db.execute(query)).first()
    if row is None:
        return None

    history_rows = (
        await db.execute(
            select(KPIHistory)
            .where(
                KPIHistory.kpi_record_id == row.record_id,
                KPIHistory.is_deleted.is_(False),
            )
            .order_by(KPIHistory.created_at, KPIHistory.id)
        )
    ).scalars().all()
    history = [
        ExecutiveEmployeeHistoryPoint(
            changed_at=item.created_at,
            percentage=item.new_percentage,
            weighted_score=item.new_weighted_score,
            status=item.new_status,
            reason=item.reason,
        )
        for item in history_rows
    ]
    return ExecutiveEmployeeDetail(
        user_id=user_id,
        employee_name=f"{row.first_name} {row.last_name}".strip(),
        department_name=row.department_name,
        position_name=row.position_name,
        level=row.user_level,
        period=_period_option(period),
        percentage=round(float(row.percentage), 2),
        status=row.status_bucket,
        raw_score=round(float(row.raw_score), 2),
        weighted_score=round(float(row.weighted_score), 2),
        formula_name=row.formula_name,
        category_intelligence=ExecutiveUnavailableData(
            reason="Category-level results are not independently persisted."
        ),
        target_intelligence=ExecutiveUnavailableData(
            reason="Authoritative target-versus-actual results are not persisted."
        ),
        history=history,
    )
