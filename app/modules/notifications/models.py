from sqlalchemy import Boolean, Column, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from app.database.base import BaseModel

class Notification(BaseModel):
    __tablename__ = "notifications"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    
    # Types: REMINDER, DEADLINE, ESCALATION, APPROVAL, DELAY, REJECT
    notification_type = Column(String(50), nullable=False)  
    is_read = Column(Boolean, default=False, index=True)
    
    # Grouped Notification နှင့် Batch Notification အတွက်
    group_key = Column(String(100), nullable=True, index=True)  
    
    # DASHBOARD, EMAIL, BOTH
    channel = Column(String(20), default="DASHBOARD")

    __table_args__ = (
        Index(
            "idx_notifications_user_deleted_created",
            "user_id",
            "is_deleted",
            "created_at",
        ),
        Index(
            "idx_notifications_user_deleted_read",
            "user_id",
            "is_deleted",
            "is_read",
        ),
    )