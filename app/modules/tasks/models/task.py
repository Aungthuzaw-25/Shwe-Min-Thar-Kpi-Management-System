"""
Enterprise Task & Workflow Engine Model
"""
import enum

from sqlalchemy import JSON, Boolean, Column, Date, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database.base import BaseModel

SET_NULL = "SET NULL"
ALL_DELETE_ORPHAN = "all, delete-orphan"


# ==============================================================================
# 🌟 1. ENUMS (Task Status & Frequency Lifecycle)
# ==============================================================================
class TaskStatus(str, enum.Enum):
    DRAFT = "Draft"                     # ရေးဆွဲဆဲ (မတင်ရသေး)
    PENDING_L1 = "Pending L1 Approval"  # L1 Supervisor သို့ တင်ပြထားသည်
    PENDING_L0 = "Pending L0 Approval"  # L0 Head သို့ ဆက်လက် တင်ပြထားသည်
    APPROVED = "Approved"               # အတည်ပြုပြီးပါပြီ
    REJECTED = "Rejected"               # ပယ်ချလိုက်သည်
    COMPLETED = "Completed"             # အပြီးသတ် အတည်ပြုပြီးစီးပါပြီ

class TaskFrequency(str, enum.Enum):
    ONCE = "ONCE"
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    YEARLY = "YEARLY"

# ==============================================================================
# 🌟 2. TASK DATA MODEL
# ==============================================================================
class Task(BaseModel):
    __tablename__ = 'tasks'

    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    
    # 🌟 Weighted KPI အတွက် Admin သတ်မှတ်ထားသော Category 
    category = Column(String(100), nullable=True, index=True) 
    category_id = Column(UUID(as_uuid=True), ForeignKey('kpi_categories.id', ondelete=SET_NULL), nullable=True, index=True)
    
    status = Column(SQLEnum(TaskStatus), default=TaskStatus.DRAFT, nullable=False, index=True)
    
    # 🌟 DUE DATE & LATE PENALTY SETTINGS (Assigned Task များအတွက်သာ အလုပ်လုပ်မည်)
    due_date = Column(DateTime(timezone=True), nullable=True, index=True)
    grace_period_days = Column(Integer, default=0, nullable=False) # ခွင့်ပြုထားသော နောက်ကျရက်
    is_late = Column(Boolean, default=False, nullable=False, index=True) # နောက်ကျမှ တင်ခြင်း ဟုတ်/မဟုတ်
    late_days = Column(Integer, default=0, nullable=False) # အမှန်တကယ် ဘယ်နှစ်ရက် နောက်ကျသွားသလဲ

    # Task တာဝန်ယူလုပ်ဆောင်သူ (L2 Staff)
    owner_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete="SET NULL"), nullable=True, index=True)

    # Owner name snapshot, so a finished task stays attributable after
    # the owner's account is removed (owner_id is then NULL).
    owner_name = Column(String(255), nullable=True)
    # Stamped by the offboarding flow; same "Resigned · <date>" badge
    # contract as kpi_records.employee_resigned_at.
    owner_resigned_at = Column(Date, nullable=True)
    # Grouping key to the departed-identity registry; same contract as
    # kpi_records.departed_identity_id.
    departed_identity_id = Column(UUID(as_uuid=True), ForeignKey('departed_identities.id', ondelete="RESTRICT"), nullable=True, index=True)

    # Outcome snapshot, stamped by the hard-delete flow BEFORE the
    # step-by-step history/review trail is purged (Zenn's rule:
    # outcomes stay, activity trails go). For a completed task:
    # {"completed_at": iso, "result": text} — the completion moment
    # comes from the history entry that moved the task to COMPLETED
    # and the result text from the final review (or that entry's
    # remarks). Lets a successor see what the departed person
    # finished without any personal activity trail surviving.
    outcome_snapshot = Column(JSON, nullable=True)
    
    # Task Assign ချပေးသူ သို့မဟုတ် လက်ရှိ စစ်ဆေးနေသော Manager (L1/L0)
    assigner_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete=SET_NULL), nullable=True, index=True)
    
    # Task Versioning (V1, V2 Resubmission)
    version = Column(Integer, default=1, nullable=False)
    parent_task_id = Column(UUID(as_uuid=True), ForeignKey('tasks.id', ondelete=SET_NULL), nullable=True, index=True)

    # Batch Submission Workflow (Multi-Task Grouping)
    batch_id = Column(UUID(as_uuid=True), ForeignKey('submission_batches.id', ondelete=SET_NULL), nullable=True, index=True)

    # --------------------------------------------------------------------------
    # 🌟 AUTOMATION & SCHEDULER FIELDS
    # --------------------------------------------------------------------------
    # ၁။ Recurring (ပုံမှန် အလိုအလျောက် ပေါ်လာမည့် Task များအတွက်)
    frequency = Column(SQLEnum(TaskFrequency), default=TaskFrequency.ONCE, nullable=False, index=True)
    next_run_date = Column(DateTime(timezone=True), nullable=True, index=True)
    
    # ၂။ Escalation (အချိန်ကျော်လွန်ပါက L0 သို့ အလိုအလျောက် တင်ပြရန်)
    is_escalated = Column(Boolean, default=False, nullable=False, index=True)
    escalated_at = Column(DateTime(timezone=True), nullable=True)
    escalated_to_level = Column(String(50), nullable=True)
    
    # ၃။ Reminders (သတိပေးချက် နောက်ဆုံးပို့ခဲ့သည့်အချိန်)
    last_reminder_sent = Column(DateTime(timezone=True), nullable=True)

    # --------------------------------------------------------------------------
    # 🌟 RELATIONSHIPS
    # --------------------------------------------------------------------------
    owner = relationship('User', foreign_keys=[owner_id], backref="owned_tasks")
    assigner = relationship('User', foreign_keys=[assigner_id], backref="assigned_tasks")
    parent_task = relationship('Task', remote_side=lambda: [Task.id], foreign_keys=[parent_task_id])
    
    # Evidence & Reviews
    evidence = relationship('TaskEvidence', back_populates='task', cascade=ALL_DELETE_ORPHAN, lazy="selectin")
    reviews = relationship('TaskReview', back_populates='task', cascade=ALL_DELETE_ORPHAN, lazy="selectin")
    
    # Submission Batch Link
    batch = relationship("SubmissionBatch", back_populates="tasks")

    # Audit Trail History
    histories = relationship("TaskHistory", backref="task", cascade=ALL_DELETE_ORPHAN, lazy="selectin")

    # --------------------------------------------------------------------------
    # ⚡ 3. PERFORMANCE COMPOUND INDEXES (HIGH-THROUGHPUT OPTIMIZATION)
    # --------------------------------------------------------------------------
    __table_args__ = (
        # User Dashboard Task List အတွက် အမြန်ဆုံး Query Index
        Index('idx_tasks_owner_status_deleted', 'owner_id', 'status', 'is_deleted'),
        
        # Background Scheduler: Recurring Task များ အမြန်ရှာဖွေရန်
        Index('idx_tasks_recurring_scheduler', 'frequency', 'next_run_date'),
        
        # Background Scheduler: Escalation Task များ အမြန်စစ်ဆေးရန်
        Index('idx_tasks_escalation_tracker', 'status', 'is_escalated', 'updated_at'),
        
        # Manager Queue Batch Lookup အတွက်
        Index('idx_tasks_batch_lookup', 'batch_id', 'status'),

        # Reminder scans filter by task state and soft deletion before due date.
        Index('idx_tasks_status_deleted_due_date', 'status', 'is_deleted', 'due_date'),
    )