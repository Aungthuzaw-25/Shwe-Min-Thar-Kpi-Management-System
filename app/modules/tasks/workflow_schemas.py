from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class BatchSubmitRequest(BaseModel):
    task_ids: list[UUID]
    reviewer_id: UUID

class BatchSubmitResponse(BaseModel):
    batch_id: UUID
    total_tasks: int
    status: str
    message: str

class QueueItemResponse(BaseModel):
    queue_id: UUID
    batch_id: UUID
    submitter_email: str
    submitter_id: UUID | None = None
    submitter_name: str | None = None
    avatar_url: str | None = None
    initials: str = "?"
    avatar_color_index: int = 0
    total_tasks: int
    pending_count: int
    submitted_at: datetime

class BatchReviewRequest(BaseModel):
    action: str  # APPROVED, REJECTED, DELAY
    comments: str | None = None
    next_reviewer_id: UUID | None = None
    deduction_rule: str | None = None  # 🌟 FIX: KPI အမှတ်လျော့မည့် ခေါင်းစဉ်ကို ထည့်သွင်းရန်

class TaskTimelineResponse(BaseModel):
    id: UUID
    action: str
    old_value: str | None = None
    new_value: str | None = None
    remarks: str | None = None
    created_at: datetime
    changer_email: str