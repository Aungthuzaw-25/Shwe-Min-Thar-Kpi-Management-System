from sqlalchemy import Column, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database.base import BaseModel


class ActivityTimeline(BaseModel):
    __tablename__ = 'activity_timelines'

    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id'), nullable=False, index=True)
    
    action = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)
    entity_id = Column(UUID(as_uuid=True), nullable=False)
    description = Column(String, nullable=True)

    __table_args__ = (
        Index("idx_activity_timelines_user_deleted_created", "user_id", "is_deleted", "created_at"),
    )

    user = relationship("User")
