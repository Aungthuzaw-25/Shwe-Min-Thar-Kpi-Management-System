from sqlalchemy import Boolean, Column, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database.base import BaseModel


class TaskReview(BaseModel):
    __tablename__ = 'task_reviews'

    task_id = Column(UUID(as_uuid=True), ForeignKey('tasks.id'), nullable=False, index=True)
    reviewer_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    reviewer_level = Column(String(10), nullable=False) # L1, L0
    
    # 🌟 3-Stage Action: Approve, Delay-Valid-Reason, Reject-Invalid-Reason
    action = Column(String(50), nullable=False) 
    comments = Column(Text, nullable=True)
    
    # 🌟 L0 Verification & Penalty
    is_verified_by_l0 = Column(Boolean, default=False)
    l0_penalty_applied = Column(Boolean, default=False) 

    task = relationship('Task', back_populates='reviews')
    reviewer = relationship('User', foreign_keys=[reviewer_id])

    __table_args__ = (
        Index('idx_task_reviews_task_level_created', 'task_id', 'reviewer_level', 'created_at'),
    )