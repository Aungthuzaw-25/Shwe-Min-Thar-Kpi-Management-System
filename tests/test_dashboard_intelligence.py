from pathlib import Path
from uuid import uuid4

from app.modules.kpi.dashboard_schemas import (
    ActivityActionCount,
    ActivitySummary,
    CategoryPerformance,
    DashboardDataQuality,
    DashboardMetric,
    DashboardNotification,
    DashboardNotifications,
    DashboardTrend,
    KPIStatusCount,
)


def test_dashboard_metric_preserves_a_real_zero():
    metric = DashboardMetric(value=0, status="available")

    assert metric.value == 0
    assert metric.status == "available"


def test_dashboard_trend_requires_comparison_data_for_delta():
    trend = DashboardTrend(
        labels=["2026-08-01", "2026-09-01"],
        values=[72.5, 78.0],
        count=2,
        current_value=78.0,
        previous_value=72.5,
        delta=5.5,
        comparison_status="available",
    )

    assert trend.delta == 5.5
    assert trend.comparison_status == "available"


def test_dashboard_trend_marks_single_period_as_insufficient():
    trend = DashboardTrend(
        labels=["2026-09-01"],
        values=[78.0],
        count=1,
        current_value=78.0,
        comparison_status="insufficient_data",
    )

    assert trend.delta is None
    assert trend.comparison_status == "insufficient_data"


def test_category_performance_is_explicitly_unavailable():
    category = CategoryPerformance(
        status="unavailable",
        reason="category_results_not_stored",
    )
    quality = DashboardDataQuality(
        status="partial",
        evaluated_kpi_records=0,
        kpi_trend="unavailable",
        department_comparison="unavailable",
        category_performance=category,
        task_data="available",
        comparison="unavailable",
    )

    assert quality.category_performance.status == "unavailable"
    assert quality.category_performance.reason == "category_results_not_stored"

def test_dashboard_contract_preserves_authoritative_activity_status_and_notifications():
    activity = ActivitySummary(
        total=3,
        by_action=[ActivityActionCount(action="TASK_CREATED", count=3)],
    )
    notifications = DashboardNotifications(
        unread_count=0,
        recent=[
            DashboardNotification(
                id=uuid4(),
                title="Task updated",
                message="A task changed",
                notification_type="TASK",
            )
        ],
    )
    status = KPIStatusCount(status="EXCELLENT", count=2)

    assert activity.total == 3
    assert notifications.unread_count == 0
    assert status.count == 2


def test_dashboard_frontend_uses_executive_contract_without_fake_analytics():
    source = (
        Path(__file__).parents[1] / "static" / "js" / "executive-dashboard.js"
    ).read_text(encoding="utf-8")

    assert "/api/v1/dashboard/executive/overview" in source
    assert "status_distribution" in source
    assert "kpiTrendAccessibleTable" in source
    assert "/api/v1/dashboard/executive/employees" in source
    assert "activity_summary" in source
    assert "notifications?.unread_count" in source
    assert "simulateAnomaly" not in source
    assert "new Function" not in source


def test_dashboard_error_panel_respects_its_hidden_state():
    source = (
        Path(__file__).parents[1] / "static" / "css" / "executive-dashboard.css"
    ).read_text(encoding="utf-8")

    assert ".executive-error[hidden] { display: none; }" in source


def test_superadmin_dashboard_handles_auth_and_server_failures_explicitly():
    source = (
        Path(__file__).parents[1]
        / "static"
        / "js"
        / "superadmin-executive-dashboard.js"
    ).read_text(encoding="utf-8")

    assert "if (!response.ok)" in source
    assert "requestFailureMessage(response.status, failureMessage)" in source
    assert "Your session has expired." in source
    assert "Dashboard access is not authorized." in source
    assert "The dashboard service is unavailable." in source
    assert "Monthly data unavailable" in source
    assert "summaryError?.status === 401 || summaryError?.status === 403" in source
