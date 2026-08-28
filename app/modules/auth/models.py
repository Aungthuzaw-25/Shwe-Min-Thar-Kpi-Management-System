from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import UUID
import uuid
from datetime import datetime, timezone
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
    attempted_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), index=True)


class TokenBlacklist(Base):
    __tablename__ = 'token_blacklist'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token = Column(String, unique=True, index=True, nullable=False)
    blacklisted_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class PasswordHistory(Base):
    __tablename__ = 'password_history'
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # 🌟 FIX: Added ondelete="CASCADE"
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete="CASCADE"), nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))