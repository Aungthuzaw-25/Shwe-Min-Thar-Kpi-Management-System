from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.kpi.models import KPIPeriodStatus, KPIStatus, PeriodType


class ExecutivePeriodOption(BaseModel):
    id: UUID
    label: str
    period_type: PeriodType | None = None
    start_date: datetime
    end_date: datetime
    status: KPIPeriodStatus


class ExecutiveStatusCount(BaseModel):
    status: KPIStatus
    count: int
    percentage: float | None


class ExecutiveSummary(BaseModel):
    status: Literal["available", "unavailable"]
    average_percentage: float | None
    evaluated_employee_count: int | None
    no_result_employee_count: int | None
    active_employee_count: int | None
    status_distribution: list[ExecutiveStatusCount] = Field(default_factory=list)


class ExecutiveDepartment(BaseModel):
    department_id: UUID
    department_name: str
    employee_count: int
    evaluated_employee_count: int
    no_result_employee_count: int
    average_percentage: float | None
    excellent_count: int
    very_good_count: int
    good_count: int
    fair_count: int
    poor_count: int
    below_fair_employee_count: int
    below_fair_percentage: float | None
    at_or_above_good_employee_count: int
    at_or_above_good_percentage: float | None


class ExecutiveTrendPoint(BaseModel):
    period_id: UUID
    period_label: str
    average_percentage: float | None
    evaluated_employee_count: int
    status_distribution: list[ExecutiveStatusCount]


class ExecutiveTrend(BaseModel):
    points: list[ExecutiveTrendPoint] = Field(default_factory=list)
    delta: float | None
    direction: Literal["UP", "DOWN", "FLAT", "UNAVAILABLE"]
    comparison_available: bool
    tolerance: float = 0


class ExecutiveMonthlyAverage(BaseModel):
    month: int = Field(ge=1, le=12)
    average_percentage: float | None
    evaluated_employee_count: int


class ExecutiveMonthlyAverageDepartment(BaseModel):
    id: UUID
    name: str


class ExecutiveMonthlyAverageResponse(BaseModel):
    departments: list[ExecutiveMonthlyAverageDepartment] = Field(default_factory=list)
    monthly_averages: list[ExecutiveMonthlyAverage] = Field(default_factory=list)


class ExecutiveSignal(BaseModel):
    signal_type: Literal["IMPROVEMENT", "DECLINE", "STABILITY", "CONCENTRATION", "COVERAGE"]
    severity: Literal["info", "warning"]
    title: str
    explanation: str
    metric_name: str
    metric_value: float
    period_id: UUID
    period_label: str
    department_id: UUID | None = None


class ExecutiveAttentionArea(BaseModel):
    area_type: Literal["STATUS_CONCENTRATION", "MISSING_RESULTS"]
    severity: Literal["info", "warning"]
    title: str
    explanation: str
    metric_name: str
    metric_value: float
    period_id: UUID
    period_label: str
    department_id: UUID | None = None


class ExecutiveUnavailableData(BaseModel):
    status: Literal["unavailable"] = "unavailable"
    reason: str


class ExecutiveOverview(BaseModel):
    periods: list[ExecutivePeriodOption] = Field(default_factory=list)
    selected_period: ExecutivePeriodOption | None = None
    summary: ExecutiveSummary
    departments: list[ExecutiveDepartment] = Field(default_factory=list)
    trend: ExecutiveTrend
    signals: list[ExecutiveSignal] = Field(default_factory=list)
    attention_areas: list[ExecutiveAttentionArea] = Field(default_factory=list)
    category_intelligence: ExecutiveUnavailableData
    target_intelligence: ExecutiveUnavailableData


class ExecutiveEmployeeHistoryPoint(BaseModel):
    changed_at: datetime | None
    percentage: float
    weighted_score: float | None
    status: KPIStatus
    reason: str


class ExecutiveEmployeeResult(BaseModel):
    user_id: UUID
    employee_name: str
    department_name: str | None
    position_name: str | None
    level: str | None
    percentage: float
    status: KPIStatus
    avatar_url: str | None = None
    initials: str = "?"
    avatar_color_index: int = 0


class ExecutiveEmployeePage(BaseModel):
    items: list[ExecutiveEmployeeResult] = Field(default_factory=list)
    total: int
    offset: int
    limit: int


class ExecutiveEmployeeDetail(BaseModel):
    user_id: UUID
    employee_name: str
    department_name: str | None
    position_name: str | None
    level: str | None
    period: ExecutivePeriodOption
    percentage: float
    status: KPIStatus
    raw_score: float
    weighted_score: float
    formula_name: str | None
    category_intelligence: ExecutiveUnavailableData
    target_intelligence: ExecutiveUnavailableData
    history: list[ExecutiveEmployeeHistoryPoint] = Field(default_factory=list)
