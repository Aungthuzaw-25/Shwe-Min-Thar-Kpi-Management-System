from sqlalchemy import Column, String, Text, JSON, Enum
import enum
from app.database.base import BaseModel

class LogLevel(enum.Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

class SystemSetting(BaseModel):
    """Global configuration for the KPI system"""
    __tablename__ = "system_settings"

    key = Column(String(100), unique=True, nullable=False, index=True)
    value = Column(JSON, nullable=False)
    description = Column(Text, nullable=True)

class SystemEventLog(BaseModel):
    """System-level background jobs and event logging"""
    __tablename__ = "system_event_logs"

    event_type = Column(String(100), nullable=False, index=True) # e.g., "MONTHLY_ROLLUP", "EMAIL_SYNC"
    level = Column(Enum(LogLevel), default=LogLevel.INFO, nullable=False)
    message = Column(Text, nullable=False)
    details = Column(JSON, nullable=True)