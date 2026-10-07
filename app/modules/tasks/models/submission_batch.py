from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database.base import BaseModel


class SubmissionBatch(BaseModel):
    __tablename__ = 'submission_batches'

    submitter_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False, index=True)
    total_tasks = Column(Integer, default=0)
    status = Column(String, default="PENDING") 

    submitter = relationship("User")
    queue = relationship("ApprovalQueue", back_populates="batch", uselist=False)
    tasks = relationship("Task", back_populates="batch")
