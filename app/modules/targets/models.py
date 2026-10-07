import enum

from sqlalchemy import Column, Enum, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database.base import BaseModel


class MetricType(enum.Enum):
    CURRENCY = "CURRENCY"
    COUNT = "COUNT"
    PERCENTAGE = "PERCENTAGE"

class TargetStatus(enum.Enum):
    ACTIVE = "ACTIVE"
    ACHIEVED = "ACHIEVED"
    MISSED = "MISSED"

class Target(BaseModel):
    __tablename__ = "targets"

    name = Column(String(200), nullable=False)
    
    # If null, it means it is a company-wide target
    department_id = Column(UUID(as_uuid=True), ForeignKey("departments.id"), nullable=True)
    
    target_value = Column(Float, nullable=False)
    metric_type = Column(Enum(MetricType), nullable=False, default=MetricType.COUNT)
    target_month = Column(String(7), nullable=False) # e.g., "2026-08"
    
    status = Column(Enum(TargetStatus), nullable=False, default=TargetStatus.ACTIVE)
    
    # Audit tracking
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    # Relationships
    department = relationship("Department")
    created_by = relationship("User")