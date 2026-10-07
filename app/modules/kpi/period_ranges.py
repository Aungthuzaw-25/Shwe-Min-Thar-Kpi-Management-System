from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.core.timezone import APPLICATION_TIMEZONE, as_application_time
from app.modules.kpi.models import PeriodType


@dataclass(frozen=True)
class CalendarPeriodRange:
    start_inclusive: datetime
    end_exclusive: datetime


def _at_local_midnight(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=APPLICATION_TIMEZONE)


def _calendar_date(value: datetime) -> date:
    if value.tzinfo is not None and value.timetz().replace(tzinfo=None) == time.min:
        return value.date()
    return as_application_time(value).date()


def normalize_calendar_range(
    start: datetime | None,
    end: datetime | None,
) -> CalendarPeriodRange:
    """Normalize date filters to app-local calendar boundaries.

    Midnight end values are already exclusive boundaries. Legacy end values
    later in the day include that calendar day and are advanced to next midnight.
    Naive datetimes follow the application's existing naive-as-UTC policy.
    """
    start_inclusive = (
        _at_local_midnight(_calendar_date(start)) if start is not None else None
    )
    end_exclusive = None
    if end is not None:
        end_date = _calendar_date(end)
        local_end = as_application_time(end)
        if end.time() != time.min and local_end.timetz().replace(tzinfo=None) != time.min:
            end_date += timedelta(days=1)
        end_exclusive = _at_local_midnight(end_date)

    if start_inclusive is not None and end_exclusive is not None:
        if start_inclusive >= end_exclusive:
            raise ValueError("period_start must be before period_end")
    return CalendarPeriodRange(start_inclusive, end_exclusive)


def calendar_period_range(
    period_type: PeriodType,
    reference: datetime | date,
) -> CalendarPeriodRange:
    """Return the calendar boundaries for a period containing ``reference``."""
    reference_date = (
        _calendar_date(reference) if isinstance(reference, datetime) else reference
    )

    if period_type == PeriodType.DAILY:
        start_date = reference_date
        end_date = start_date + timedelta(days=1)
    elif period_type == PeriodType.WEEKLY:
        start_date = reference_date - timedelta(days=reference_date.weekday())
        end_date = start_date + timedelta(days=7)
    elif period_type == PeriodType.MONTHLY:
        start_date = reference_date.replace(day=1)
        end_date = (
            start_date.replace(year=start_date.year + 1, month=1)
            if start_date.month == 12
            else start_date.replace(month=start_date.month + 1)
        )
    elif period_type == PeriodType.QUARTERLY:
        quarter_start_month = ((reference_date.month - 1) // 3) * 3 + 1
        start_date = reference_date.replace(month=quarter_start_month, day=1)
        end_month = quarter_start_month + 3
        end_date = (
            start_date.replace(year=start_date.year + 1, month=end_month - 12)
            if end_month > 12
            else start_date.replace(month=end_month)
        )
    elif period_type == PeriodType.YEARLY:
        start_date = reference_date.replace(month=1, day=1)
        end_date = start_date.replace(year=start_date.year + 1)
    else:
        raise ValueError(f"Unsupported KPI period type: {period_type}")

    return CalendarPeriodRange(
        _at_local_midnight(start_date),
        _at_local_midnight(end_date),
    )
