from sqlalchemy import Column, ForeignKey, Index, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database.base import BaseModel


class ApprovalQueue(BaseModel):
    __tablename__ = 'approval_queues'

    batch_id = Column(UUID(as_uuid=True), ForeignKey('submission_batches.id'), nullable=False ,index=True)
    reviewer_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False, index=True)

    pending_count = Column(Integer, default=0)
    completed_count = Column(Integer, default=0)
    partial_count = Column(Integer, default=0)
    blocked_count = Column(Integer, default=0)

    __table_args__ = (
        Index("idx_approval_queues_reviewer_created", "reviewer_id", "created_at"),
    )

    batch = relationship("SubmissionBatch", back_populates="queue")
    reviewer = relationship("User")
