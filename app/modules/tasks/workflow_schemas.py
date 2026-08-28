from pydantic import BaseModel
from typing import List, Optional
from uuid import UUID
from datetime import datetime

class BatchSubmitRequest(BaseModel):
    task_ids: List[UUID]
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
    total_tasks: int
    pending_count: int
    submitted_at: datetime

class BatchReviewRequest(BaseModel):
    action: str  # APPROVED, REJECTED, DELAY
    comments: Optional[str] = None
    next_reviewer_id: Optional[UUID] = None 
    deduction_rule: Optional[str] = None  # 🌟 FIX: KPI အမှတ်လျော့မည့် ခေါင်းစဉ်ကို ထည့်သွင်းရန်

class TaskTimelineResponse(BaseModel):
    id: UUID
    action: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    remarks: Optional[str] = None
    created_at: datetime
    changer_email: str