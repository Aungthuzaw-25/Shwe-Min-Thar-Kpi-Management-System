from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.modules.kpi.models import ActionPlanStatus


class ActionPlanCreate(BaseModel):
    kpi_record_id: UUID
    description: str
    target_completion_date: datetime

class ActionPlanReview(BaseModel):
    status: ActionPlanStatus # APPROVED or REJECTED
    manager_feedback: str | None = None