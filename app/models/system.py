import enum

from sqlalchemy import JSON, Column, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.database.base import BaseModel


class WhatsNewVisibility(str, enum.Enum):
    """Audience family for a what's-new entry (relevance filter,
    NOT a security boundary — entries contain no sensitive info)."""

    ALL = "ALL"
    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"
    EMPLOYEE = "EMPLOYEE"


class WhatsNewStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"


class WhatsNewEntry(BaseModel):
    """A what's-new announcement (Burmese-only for launch).

    Stored as String columns + Python str-Enums (no PG enum type
    to manage). Soft-delete via BaseModel.is_deleted.
    """

    __tablename__ = "whats_new_entries"

    title = Column(String(200), nullable=False)
    body = Column(Text, nullable=False)
    how_to = Column(Text, nullable=True)
    link_path = Column(String(255), nullable=True)
    link_label = Column(String(100), nullable=True)
    image_path = Column(String(255), nullable=True)
    visible_to = Column(
        String(20), nullable=False, default="ALL", index=True
    )
    status = Column(String(20), nullable=False, default="DRAFT", index=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    created_by_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


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