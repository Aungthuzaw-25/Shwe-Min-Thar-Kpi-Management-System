from sqlalchemy import Column, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database.base import BaseModel


class TaskEvidence(BaseModel):
    __tablename__ = 'task_evidence'

    task_id = Column(UUID(as_uuid=True), ForeignKey('tasks.id'), nullable=False, index=True)
    file_name = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_hash = Column(String(64), nullable=False) 
    uploaded_by = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)

    task = relationship('Task', back_populates='evidence')
    uploader = relationship('User', foreign_keys=[uploaded_by])
