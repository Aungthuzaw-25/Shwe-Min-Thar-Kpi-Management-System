import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

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