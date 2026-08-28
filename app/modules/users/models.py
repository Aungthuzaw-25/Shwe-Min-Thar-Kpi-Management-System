from sqlalchemy import Column, String, Boolean, Integer, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database.base import BaseModel
import uuid

# ==============================================================================
# 🌟 Role Model (Permission များနှင့် Many-to-Many ချိတ်ဆက်ထားသည်)
# ==============================================================================
class Role(BaseModel):
    __tablename__ = "roles"
    
    name = Column(String(100), unique=True, index=True, nullable=False)
    description = Column(String(255), nullable=True)
    
    # 🌟 NEW: Role တစ်ခုတွင် ပါဝင်သော Permission များကို အလွယ်တကူ ခေါ်ယူနိုင်ရန်
    permissions = relationship(
        "Permission",
        secondary="role_permissions",
        backref="roles",
        lazy="selectin" # Eager loading အတွက် (Query မြန်ဆန်စေရန်)
    )

# ==============================================================================
# မူလ User Model (မည်သည့် Code မှ မဖျက်ထားပါ)
# ==============================================================================
class User(BaseModel):
    __tablename__ = "users"
    
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    
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

    role_id = Column(UUID(as_uuid=True), ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)
    permission_id = Column(UUID(as_uuid=True), ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True)

    role = relationship("Role", backref="role_permissions_link")
    permission = relationship("Permission")