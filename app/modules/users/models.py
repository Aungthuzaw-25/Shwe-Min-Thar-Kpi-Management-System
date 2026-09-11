from sqlalchemy import CheckConstraint, Column, String, Boolean, Integer, ForeignKey, DateTime, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, backref
from app.database.base import BaseModel
import uuid

# ──────────────────────────────────────
# Constants for repeated string literals
# ──────────────────────────────────────
ROLE_TABLE = "roles"
USER_TABLE = "users"
ROLE_PERMISSION_TABLE = "role_permissions"
PERMISSION_TABLE = "permissions"
PERMISSIONS_ROLES_OVERLAP = "permissions,roles"


# ==============================================================================
# 🌟 Role Model (Permission များနှင့် Many-to-Many ချိတ်ဆက်ထားသည်)
# ==============================================================================
class Role(BaseModel):
    __tablename__ = ROLE_TABLE
    
    name = Column(String(100), unique=True, index=True, nullable=False)
    description = Column(String(255), nullable=True)
    
    # 🌟 NEW: Role တစ်ခုတွင် ပါဝင်သော Permission များကို အလွယ်တကူ ခေါ်ယူနိုင်ရန်
    permissions = relationship(
        "Permission",
        secondary="role_permissions",
        backref=backref("roles", overlaps=PERMISSIONS_ROLES_OVERLAP),
        lazy="selectin",
        overlaps=PERMISSIONS_ROLES_OVERLAP
    )

# ==============================================================================
# မူလ User Model (မည်သည့် Code မှ မဖျက်ထားပါ)
# ==============================================================================
class User(BaseModel):
    __tablename__ = "users"
    
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=True)
    local_password_enabled = Column(Boolean, nullable=False, default=True, server_default="true")
    
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    position_id = Column(UUID(as_uuid=True), ForeignKey("positions.id"), nullable=True)
    
    region_town = Column(String(100), nullable=True) 
    
    is_active = Column(Boolean, default=True, index=True)
    is_locked = Column(Boolean, default=False)
    failed_login_attempts = Column(Integer, default=0)
    lockout_until = Column(DateTime, nullable=True)
    
    must_change_password = Column(Boolean, default=True)
    
    is_on_leave = Column(Boolean, default=False)
    leave_start_date = Column(DateTime, nullable=True)
    leave_end_date = Column(DateTime, nullable=True)
    delegated_to_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    
    role_id = Column(UUID(as_uuid=True), ForeignKey("roles.id"), nullable=False, index=True)
    
    role = relationship("Role")
    position = relationship("Position", back_populates="users")
    user_departments = relationship("UserDepartment", back_populates="user", cascade="all, delete-orphan")
    oauth_accounts = relationship("OAuthAccount", back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(
            "(local_password_enabled = TRUE AND hashed_password IS NOT NULL) "
            "OR (local_password_enabled = FALSE AND hashed_password IS NULL)",
            name="ck_users_local_password_consistency",
        ),
    )

# ==============================================================================
# မူလ Position Model (မည်သည့် Code မှ မဖျက်ထားပါ)
# ==============================================================================
class Position(BaseModel):
    """ရာထူးအဆင့်ဆင့် စီမံခန့်ခွဲရန် Model"""
    __tablename__ = "positions"

    name = Column(String(100), nullable=False, unique=True)
    department_id = Column(UUID(as_uuid=True), ForeignKey("departments.id"), nullable=False, index=True)
    parent_position_id = Column(UUID(as_uuid=True), ForeignKey("positions.id"), nullable=True)
    is_active = Column(Boolean, default=True)

    # 🌟 FIX: backref မှ back_populates သို့ ပြောင်းလဲထားပါသည်
    department = relationship("Department", back_populates="positions")
    users = relationship("User", back_populates="position")
    parent_position = relationship("Position", remote_side="Position.id", backref="sub_positions")

# ==============================================================================
# 🌟 Granular RBAC Models (အသစ်ထည့်သွင်းထားသော Models များ)
# ==============================================================================
class Permission(BaseModel):
    """Granular permission codes (e.g., 'kpi.approve', 'report.export')"""
    __tablename__ = "permissions"

    code = Column(String(100), unique=True, nullable=False, index=True)
    description = Column(String(255), nullable=True)


class RolePermission(BaseModel):
    """Pivot table mapping roles to specific permissions"""
    __tablename__ = "role_permissions"

    # Keep the audit identifier, but make the association keys the ORM identity.
    id = Column(UUID(as_uuid=True), default=uuid.uuid4, nullable=False, unique=True)
    role_id = Column(UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id = Column(UUID(as_uuid=True), ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True)

    role = relationship(
        "Role",
        backref=backref("role_permissions_link", overlaps=PERMISSIONS_ROLES_OVERLAP),
        overlaps=PERMISSIONS_ROLES_OVERLAP
    )
    permission = relationship(
        "Permission",
        overlaps=PERMISSIONS_ROLES_OVERLAP
    )


class UserPermissionOverride(BaseModel):
    """Explicit allow/deny overrides for one user, taking precedence over role grants."""
    __tablename__ = "user_permission_overrides"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    permission_id = Column(UUID(as_uuid=True), ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False, index=True)
    allowed = Column(Boolean, nullable=False)
    scope = Column(String(20), nullable=False, default="ALL")
    department_id = Column(UUID(as_uuid=True), ForeignKey("departments.id", ondelete="CASCADE"), nullable=True, index=True)

    user = relationship("User", backref="permission_overrides")
    permission = relationship("Permission")
    department = relationship("Department")

    __table_args__ = (
        UniqueConstraint("user_id", "permission_id", "department_id", name="uq_user_permission_scope"),
    )


class OAuthAccount(BaseModel):
    """An immutable external identity linked to one internal user."""

    __tablename__ = "oauth_accounts"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    provider = Column(String(32), nullable=False)
    issuer = Column(String(255), nullable=False)
    tenant_id = Column(String(128), nullable=False, default="")
    provider_subject = Column(String(255), nullable=False)
    provider_email = Column(String(255), nullable=True, index=True)
    email_verified = Column(Boolean, nullable=False, default=False, server_default="false")
    linked_at = Column(DateTime, nullable=False)
    last_login_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True, index=True)

    user = relationship("User", back_populates="oauth_accounts")

    __table_args__ = (
        UniqueConstraint(
            "provider",
            "issuer",
            "tenant_id",
            "provider_subject",
            name="uq_oauth_account_provider_subject",
        ),
        UniqueConstraint(
            "user_id",
            "provider",
            name="uq_oauth_account_user_provider",
        ),
        Index("ix_oauth_accounts_provider_lookup", "provider", "issuer", "tenant_id"),
    )