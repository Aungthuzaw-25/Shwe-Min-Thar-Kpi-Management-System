from pydantic import BaseModel, ConfigDict, Field
from typing import Optional, List
from uuid import UUID
from datetime import datetime
from app.modules.tasks.models.task import TaskStatus

# ==============================================================================
# 1. Task Base & Create/Update 
# ==============================================================================
class TaskBase(BaseModel):
    title: str = Field(..., max_length=255)
    description: Optional[str] = None
    due_date: Optional[datetime] = None
    grace_period_days: Optional[int] = Field(0, ge=0, description="Assigned task အတွက်သာ အသုံးပြုမည်")

class TaskCreate(TaskBase):
    owner_id: Optional[UUID] = None 
    category: str = Field(..., description="Admin သတ်မှတ်ထားသော Category ထဲမှ ရွေးချယ်ရန်")

class TaskItemCreate(TaskBase):
    owner_id: Optional[UUID] = None

class BulkTaskCreate(BaseModel):
    category: str = Field(..., description="ဤ Task အားလုံးအတွက် သက်ဆိုင်ရာ Category တစ်ခုတည်း")
    tasks: List[TaskItemCreate] = Field(..., description="အဆိုပါ Category အောက်တွင် ထည့်သွင်းမည့် Task စာရင်း")

class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    due_date: Optional[datetime] = None
    grace_period_days: Optional[int] = Field(None, ge=0)
    status: Optional[TaskStatus] = None
    category: Optional[str] = None

# ==============================================================================
# 2. Task Review (L1 / L0 မှ Approve/Reject/Delay လုပ်ခြင်း)
# ==============================================================================
class TaskReviewInput(BaseModel):
    # 🌟 FIX: action နှင့် deduction_rule အား အသစ်ထည့်သွင်းထားသည်
    action: str = Field(..., description="APPROVED, REJECTED, သို့မဟုတ် DELAY")
    comments: str = Field(..., description="စစ်ဆေးသည့် မှတ်ချက် (Delay/Reject တွင် မဖြစ်မနေ ထည့်ရန်)")
    deduction_rule: Optional[str] = Field(None, description="Reject လုပ်ပါက KPI ဖြတ်မည့် ခေါင်းစဉ်")

class TaskReviewResponse(BaseModel):
    id: UUID
    task_id: UUID
    reviewer_id: UUID
    reviewer_level: str
    action: str
    comments: Optional[str]
    is_verified_by_l0: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

# ==============================================================================
# 3. Task Evidence (သာဓကဖိုင်များ)
# ==============================================================================
class TaskEvidenceResponse(BaseModel):
    id: UUID
    file_name: str
    file_path: str
    file_hash: str
    uploaded_by: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

# ==============================================================================
# 4. Task Response (အထွက် Data)
# ==============================================================================
class TaskResponse(TaskBase):
    id: UUID
    status: TaskStatus
    owner_id: UUID
    assigner_id: Optional[UUID] = None
    category: str 
    version: int
    parent_task_id: Optional[UUID] = None
    created_at: datetime
    
    reviews: List[TaskReviewResponse] = []
    evidence: List[TaskEvidenceResponse] = []
    
    model_config = ConfigDict(from_attributes=True)