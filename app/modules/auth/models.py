import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.database.base import BaseModel
from app.database.connection import Base

# ==============================================================================
# 🌟 Auth Module Data Models (Single Source of Truth)
# ==============================================================================

class LoginHistory(Base):
    __tablename__ = 'login_history'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # 🌟 FIX: Added ondelete="CASCADE" to clean up history if user is deleted
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete="CASCADE"), nullable=False, index=True)
    
    ip_address = Column(String(50), nullable=True)
    user_agent = Column(String(255), nullable=True)
    is_success = Column(Boolean, default=True)
    attempted_at = Column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None), index=True)

    __table_args__ = (
        Index("idx_login_history_user_attempted", "user_id", "attempted_at"),
    )


class TokenBlacklist(Base):
    __tablename__ = 'token_blacklist'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Only a digest is persisted.  Bearer/refresh tokens are credentials and
    # must never be recoverable from the database.
    token_hash = Column(String(64), unique=True, index=True, nullable=False)
    blacklisted_at = Column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))


class AuthSession(Base):
    """One login/device session; refresh-token rotation is anchored here."""
    __tablename__ = "auth_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    refresh_token_hash = Column(String(64), nullable=False, index=True)
    device_id = Column(String(128), nullable=True)
    user_agent = Column(String(255), nullable=True)
    ip_address = Column(String(50), nullable=True)
    remember_me = Column(Boolean, nullable=False, default=False, server_default="false")
    expires_at = Column(DateTime, nullable=False, index=True)
    last_used_at = Column(DateTime, nullable=False)
    last_reauthenticated_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True, index=True)

    __table_args__ = (
        UniqueConstraint("refresh_token_hash"),
        Index("idx_auth_sessions_active_user", "user_id", "revoked_at", "expires_at"),
    )


class PasswordHistory(Base):
    __tablename__ = 'password_history'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # 🌟 FIX: Added ondelete="CASCADE"
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete="CASCADE"), nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(UTC).replace(tzinfo=None))


class UserSecuritySettings(BaseModel):
    __tablename__ = "user_security_settings"

    singleton_key = Column(Boolean, nullable=False, unique=True, default=True, server_default=text("true"))
    max_failed_attempts = Column(Integer, nullable=False, default=5, server_default="5")
    lockout_duration_minutes = Column(Integer, nullable=False, default=29, server_default="29")
    superadmin_lockout_enabled = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    updated_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None))

    __table_args__ = (
        CheckConstraint("max_failed_attempts >= 1", name="ck_user_security_max_failed_attempts"),
        CheckConstraint("lockout_duration_minutes >= 1", name="ck_user_security_lockout_duration"),
    )


class UnlockRequest(BaseModel):
    __tablename__ = "unlock_requests"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    ip_address = Column(String(50), nullable=True)
    user_agent = Column(String(255), nullable=True)
    failed_attempts = Column(Integer, nullable=False)
    locked_at = Column(DateTime, nullable=False)
    status = Column(String(16), nullable=False, default="PENDING", server_default="PENDING")
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('PENDING', 'RESOLVED')", name="ck_unlock_requests_status"),
        Index(
            "uq_unlock_requests_pending_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
            sqlite_where=text("status = 'PENDING'"),
        ),
    )