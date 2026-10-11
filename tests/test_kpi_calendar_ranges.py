from datetime import UTC, date, datetime, time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import DateTime, String, column, select, table

from app.core.timezone import APPLICATION_TIMEZONE
from app.modules.kpi.dashboard_service import (
    PERSISTED_KPI_PERIOD_STATUSES,
    _period_filters,
)
from app.modules.kpi.models import KPIPeriodStatus, PeriodType
from app.modules.kpi.period_ranges import (
    calendar_period_range,
    normalize_calendar_range,
)


def app_time(year, month, day, hour=0, minute=0, second=0, microsecond=0):
    return datetime(
        year,
        month,
        day,
        hour,
        minute,
        second,
        microsecond,
        tzinfo=APPLICATION_TIMEZONE,
    )


@pytest.mark.parametrize(
    ("period_type", "reference", "expected_start", "expected_end"),
    [
        (PeriodType.DAILY, date(2026, 6, 30), date(2026, 6, 30), date(2026, 7, 1)),
        (PeriodType.WEEKLY, date(2026, 6, 30), date(2026, 6, 29), date(2026, 7, 6)),
        (PeriodType.MONTHLY, date(2026, 6, 30), date(2026, 6, 1), date(2026, 7, 1)),
        (PeriodType.QUARTERLY, date(2026, 6, 30), date(2026, 4, 1), date(2026, 7, 1)),
        (PeriodType.YEARLY, date(2026, 6, 30), date(2026, 1, 1), date(2027, 1, 1)),
    ],
)
def test_supported_period_types_resolve_to_calendar_boundaries(
    period_type, reference, expected_start, expected_end
):
    period_range = calendar_period_range(period_type, reference)

    assert period_range.start_inclusive == datetime.combine(
        expected_start, time.min, tzinfo=APPLICATION_TIMEZONE
    )
    assert period_range.end_exclusive == datetime.combine(
        expected_end, time.min, tzinfo=APPLICATION_TIMEZONE
    )


def test_dashboard_calendar_range_includes_start_and_excludes_next_boundary():
    period_range = normalize_calendar_range(
        app_time(2026, 6, 1),
        app_time(2026, 7, 1),
    )
    start = period_range.start_inclusive
    end = period_range.end_exclusive

    assert start == app_time(2026, 6, 1)
    assert end == app_time(2026, 7, 1)
    assert start <= app_time(2026, 6, 1) < end
    assert not start <= app_time(2026, 5, 31, 23, 59, 59, 999999) < end
    assert start <= app_time(2026, 6, 30, 23, 59, 59) < end
    assert start <= app_time(2026, 6, 30, 23, 59, 59, 999999) < end
    assert not start <= app_time(2026, 7, 1) < end
    assert not start <= app_time(2026, 7, 1, microsecond=1) < end


def test_dashboard_legacy_end_of_day_is_normalized_to_next_midnight():
    period_range = normalize_calendar_range(
        app_time(2026, 6, 1),
        app_time(2026, 6, 30, 23, 59, 59, 999999),
    )

    assert period_range.end_exclusive == app_time(2026, 7, 1)


def test_utc_serialized_application_midnight_remains_exclusive():
    local_start = app_time(2026, 6, 1).astimezone(UTC)
    local_end = app_time(2026, 7, 1).astimezone(UTC)
    period_range = normalize_calendar_range(local_start, local_end)

    assert period_range.start_inclusive == app_time(2026, 6, 1)
    assert period_range.end_exclusive == app_time(2026, 7, 1)


def test_dashboard_record_filter_preserves_full_containment_semantics():
    period_range = normalize_calendar_range(
        app_time(2026, 6, 1),
        app_time(2026, 7, 1),
    )
    records = table(
        "records",
        column("period_type", String),
        column("period_start", DateTime(timezone=True)),
        column("period_end", DateTime(timezone=True)),
    )
    statement = _period_filters(
        select(records),
        records.c.period_type,
        records.c.period_start,
        records.c.period_end,
        PeriodType.MONTHLY,
        period_range.start_inclusive,
        period_range.end_exclusive,
    )
    sql = str(statement)

    assert "records.period_start >=" in sql
    assert "records.period_end <=" in sql
    contained_record_start = period_range.start_inclusive
    contained_record_end = period_range.end_exclusive
    assert contained_record_start >= period_range.start_inclusive
    assert contained_record_end <= period_range.end_exclusive
    before_range_record_start = app_time(2026, 5, 31, 23, 59, 59)
    before_range_record_end = app_time(2026, 6, 15)
    assert not (
        before_range_record_start >= period_range.start_inclusive
        and before_range_record_end <= period_range.end_exclusive
    )
    crossing_record_start = app_time(2026, 6, 30, 23)
    crossing_record_end = app_time(2026, 7, 1, 1)
    assert not (
        crossing_record_start >= period_range.start_inclusive
        and crossing_record_end <= period_range.end_exclusive
    )


def test_period_creation_accepts_exclusive_boundaries_and_legacy_day_end():
    canonical = normalize_calendar_range(
        app_time(2026, 6, 1),
        app_time(2026, 7, 1),
    )
    legacy = normalize_calendar_range(
        app_time(2026, 6, 1),
        app_time(2026, 6, 30, 23, 59, 59),
    )

    assert canonical == legacy


def test_unbounded_dashboard_filter_remains_unbounded():
    period_range = normalize_calendar_range(None, None)
    assert period_range.start_inclusive is None
    assert period_range.end_exclusive is None


def test_persisted_aggregate_status_policy_excludes_open_periods():
    assert PERSISTED_KPI_PERIOD_STATUSES == (
        KPIPeriodStatus.FINALIZED,
        KPIPeriodStatus.LOCKED,
    )
    assert KPIPeriodStatus.OPEN not in PERSISTED_KPI_PERIOD_STATUSES


@pytest.mark.asyncio
async def test_dashboard_summary_api_forwards_explicit_calendar_boundaries(monkeypatch):
    from app.modules.kpi import dashboard_router

    user = SimpleNamespace(
        id=uuid4(),
        role=SimpleNamespace(name="Department User"),
    )

    class EmptyMembershipResult:
        def scalars(self):
            return self

        def all(self):
            return []

    class ReadOnlyDatabase:
        async def execute(self, _statement):
            return EmptyMembershipResult()

    captured = {}
    expected_response = object()

    async def get_scope_user_ids(_db, _user):
        return [user.id]

    async def build_summary(_db, _user, **kwargs):
        captured.update(kwargs)
        return expected_response

    monkeypatch.setattr(dashboard_router, "_get_scope_user_ids", get_scope_user_ids)
    monkeypatch.setattr(dashboard_router, "build_dashboard_summary", build_summary)

    start = app_time(2026, 6, 1)
    end = app_time(2026, 7, 1)
    response = await dashboard_router.fetch_unified_dashboard_summary(
        period_type=PeriodType.MONTHLY,
        period_start=start,
        period_end=end,
        db=ReadOnlyDatabase(),
        current_user=user,
    )

    assert response is expected_response
    assert captured["period_type"] == PeriodType.MONTHLY
    assert captured["period_start"] == start
    assert captured["period_end"] == end


def test_executive_period_picker_uses_existing_periods_and_creation_uses_exclusive_end():
    root = Path(__file__).parents[1]
    dashboard_template = (
        root / "templates" / "dashboard" / "executive_dashboard_content.njk"
    ).read_text(encoding="utf-8")
    dashboard_script = (
        root / "static" / "js" / "executive-dashboard.js"
    ).read_text(encoding="utf-8")
    period_template = (
        root / "templates" / "dashboard" / "kpi_periods.njk"
    ).read_text(encoding="utf-8")

    assert 'id="dashboardPeriod"' in dashboard_template
    assert "/api/v1/dashboard/executive/overview" in dashboard_script
    assert "overview.periods" in dashboard_script
    assert "period_id" in dashboard_script
    assert "setUTCDate(exclusiveEnd.getUTCDate() + 1)" in period_template
    assert "end_date: `${endInput}T23:59:59Z`" not in period_template
