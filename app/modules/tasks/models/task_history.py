from sqlalchemy import Column, String, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database.base import BaseModel

class TaskHistory(BaseModel):
    __tablename__ = 'task_histories'

    task_id = Column(UUID(as_uuid=True), ForeignKey('tasks.id'), nullable=False)
    changed_by = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False)
    
    action = Column(String, nullable=False)
    old_value = Column(String, nullable=True)
    new_value = Column(String, nullable=True)
    remarks = Column(String, nullable=True)

    user = relationship("User")
