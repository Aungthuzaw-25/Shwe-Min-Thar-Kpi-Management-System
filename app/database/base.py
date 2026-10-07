import uuid

from sqlalchemy import Boolean, Column, DateTime, text
from sqlalchemy.dialects.postgresql import UUID

from app.core.timezone import utc_now_naive
from app.database.connection import Base


class BaseModel(Base):
    __abstract__ = True
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    is_deleted = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    created_at = Column(DateTime, default=utc_now_naive)
    updated_at = Column(DateTime, nullable=True, onupdate=utc_now_naive)
