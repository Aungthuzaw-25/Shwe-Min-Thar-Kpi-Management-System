import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
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
    # SA recovery: True only for a session created by a TOTP
    # recovery login. Such a session may reset the SA's password
    # once without the old password, then the flag is cleared.
    # Normal sessions are always False.
    via_recovery = Column(Boolean, nullable=False, default=False, server_default="false")

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

class SATotpSecret(BaseModel):
    """One TOTP (Google Authenticator) enrollment per Super Admin.

    The secret is stored ENCRYPTED (Fernet, server key from
    ``TOTP_ENCRYPTION_KEY``) — never plaintext, never hashed (the
    server must compute expected codes to verify). ``is_confirmed``
    is False from setup until the SA proves the first code, so an
    abandoned setup never counts as MFA. Clearing the row
    (peer-confirmed reset/disable) removes MFA. Codes are valid
    for their 30-second window (±1 step for clock drift), and a
    password login consumes its step (strict no-reuse — see
    sa_totp_used_steps; setup confirm never consumes). The
    5-wrong -> 1-hour freeze is the brute-force brake.
    """

    __tablename__ = "sa_totp_secrets"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    secret_encrypted = Column(String(512), nullable=False)
    is_confirmed = Column(Boolean, nullable=False, default=False, server_default="false")
    confirmed_at = Column(DateTime, nullable=True)


class SATotpAttempt(BaseModel):
    """Brute-force brake for TOTP verification (login step + recovery).

    One row per SA: 5 wrong codes freeze TOTP verification for 1
    hour for that account only — password login is unaffected, and
    a successful verification clears the row. An expired freeze
    resets on the next attempt (a fresh start, not a permanent lock).
    """

    __tablename__ = "sa_totp_attempts"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    failed_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    frozen_until = Column(DateTime, nullable=True)


class SATotpUsedStep(BaseModel):
    """Strict no-reuse (Zenn, 2026-10-26): 30-second steps consumed
    by a successful password login. A code matching an already
    consumed step is rejected even inside its window. ONLY login
    uses are recorded — setup confirm is verify-only and never
    consumes a step, so enroll (confirm code X) -> immediate login
    (code X) still works. Rows older than the accepted window are
    pruned on each login check, so the table stays tiny; the
    (user_id, step) unique pair also fails a concurrent double-use
    closed (the losing insert hits the constraint).
    """

    __tablename__ = "sa_totp_used_steps"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    step = Column(BigInteger, nullable=False)
    used_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC).replace(tzinfo=None))

    __table_args__ = (
        UniqueConstraint("user_id", "step", name="uq_sa_totp_used_steps_user_step"),
    )


class SAMfaRequest(BaseModel):
    """A peer-confirmed MFA request for one SA (kind RESET or DISABLE).

    RESET = phone lost (public request, no session needed).
    DISABLE = the SA wants MFA off (authed request, self only).
    Either way, the target can never resolve their own request:
    approval/rejection always needs ANOTHER Super Admin. One
    PENDING request per target at a time (partial unique index,
    mirroring unlock_requests). Approving clears the target's TOTP
    secret + attempts and revokes ALL of the target's sessions.
    """

    __tablename__ = "sa_mfa_requests"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    kind = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False, default="PENDING", server_default="PENDING")
    resolved_at = Column(DateTime, nullable=True)
    resolved_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "kind IN ('RESET', 'DISABLE')", name="ck_sa_mfa_requests_kind"
        ),
        CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'REJECTED')",
            name="ck_sa_mfa_requests_status",
        ),
        Index(
            "uq_sa_mfa_requests_pending_user",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
            sqlite_where=text("status = 'PENDING'"),
        ),
    )
