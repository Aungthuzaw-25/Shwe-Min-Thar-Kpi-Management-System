from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.targets.models import MetricType, TargetStatus


class TargetCreate(BaseModel):
    name: str = Field(..., max_length=200, description="Objective Name")
    department_id: UUID | None = Field(None, description="Target Department ID (Null for Company-wide)")
    target_value: float = Field(..., description="Numeric value to achieve")
    metric_type: MetricType
    target_month: str = Field(..., description="Format: YYYY-MM")

class TargetResponse(BaseModel):
    id: UUID
    name: str
    department_id: UUID | None
    department_name: str | None = None
    target_value: float
    metric_type: MetricType
    target_month: str
    status: TargetStatus
    created_by_id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)