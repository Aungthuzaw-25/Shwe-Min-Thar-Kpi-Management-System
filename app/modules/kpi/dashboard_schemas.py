from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.kpi.models import PeriodType


class DashboardMetric(BaseModel):
    value: float | None = None
    status: str = "available"


class DashboardScope(BaseModel):
    role: str
    level: str | None = None
    department_ids: list[UUID] = Field(default_factory=list)
    user_count: int


class DashboardPeriod(BaseModel):
    period_type: PeriodType
    start: datetime | None = None
    end: datetime | None = None


class DashboardScorecards(BaseModel):
    kpi_score: DashboardMetric
    kpi_percentage: DashboardMetric
    evaluated_kpi_records: int
    employee_count: int
    department_count: int
    task_completion_percentage: DashboardMetric
    pending_approvals: int


class DashboardTrend(BaseModel):
    labels: list[str]
    values: list[float | None]
    count: int
    current_value: float | None = None
    previous_value: float | None = None
    delta: float | None = None
    comparison_status: str = "unavailable"


class KPIStatusCount(BaseModel):
    """Count of finalized KPI records in one authoritative status bucket."""

    status: str
    count: int


class ActivityActionCount(BaseModel):
    """Count of visible timeline entries grouped by their recorded action."""

    action: str
    count: int


class ActivitySummary(BaseModel):
    total: int
    by_action: list[ActivityActionCount] = Field(default_factory=list)


class DashboardNotification(BaseModel):
    id: UUID
    title: str
    message: str
    notification_type: str
    created_at: datetime | None = None


class DashboardNotifications(BaseModel):
    unread_count: int
    recent: list[DashboardNotification] = Field(default_factory=list)


class DepartmentComparison(BaseModel):
    department_id: UUID
    department_name: str
    employee_count: int
    kpi_score: DashboardMetric
    kpi_percentage: DashboardMetric
    evaluated_count: int


class TaskDiagnostics(BaseModel):
    total: int
    draft: int
    pending: int
    pending_approval: int
    approved: int
    completed: int
    rejected: int
    overdue: int
    late: int
    completion_percentage: DashboardMetric
    overdue_rate: DashboardMetric
    late_rate: DashboardMetric


class ApprovalDiagnostics(BaseModel):
    pending_l1: int
    pending_l0: int
    total_pending_approval: int
    rejected: int


class DashboardAlert(BaseModel):
    severity: str
    type: str
    message: str
    count: int
    source: str = "dashboard"
    period: str | None = None


class CategoryPerformance(BaseModel):
    status: str
    reason: str | None = None


class DashboardDataQuality(BaseModel):
    status: str
    evaluated_kpi_records: int
    kpi_trend: str
    department_comparison: str
    category_performance: CategoryPerformance
    task_data: str
    comparison: str
    period_notes: list[str] = Field(default_factory=list)
    metrics_unavailable: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class DashboardSummaryResponse(BaseModel):
    scope: DashboardScope
    period: DashboardPeriod
    scorecards: DashboardScorecards
    trends: dict[str, DashboardTrend]
    kpi_status_distribution: list[KPIStatusCount]
    department_comparison: list[DepartmentComparison]
    task_diagnostics: TaskDiagnostics
    approval_diagnostics: ApprovalDiagnostics
    category_performance: CategoryPerformance
    alerts: list[DashboardAlert]
    activity: list[dict[str, Any]]
    activity_summary: ActivitySummary
    notifications: DashboardNotifications
    data_quality: DashboardDataQuality

    model_config = ConfigDict(from_attributes=True)
