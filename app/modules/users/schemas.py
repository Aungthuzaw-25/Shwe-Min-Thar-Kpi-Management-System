from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class RoleResponse(BaseModel):
    id: UUID
    name: str

class UserDeptResponse(BaseModel):
    id: UUID
    name: str
    level: str
    is_head: bool

class PositionResponseShort(BaseModel):
    id: UUID
    name: str

class UserResponse(BaseModel):
    id: UUID
    first_name: str
    last_name: str
    email: EmailStr
    position: PositionResponseShort | None = None
    region_town: str | None = None
    is_active: bool
    role: RoleResponse | None = None
    departments: list[UserDeptResponse] = []
    created_at: datetime | None = None
    last_login: datetime | None = None

    class Config:
        from_attributes = True

class UserCreate(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    password: str | None = None
    position_id: UUID | None = None
    region_town: str | None = None
    role_id: UUID
    is_active: bool = True
    department_id: UUID | None = None
    user_level: str | None = 'L2'

class UserUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    email: EmailStr | None = None
    position_id: UUID | None = None
    region_town: str | None = None
    role_id: UUID | None = None
    is_active: bool | None = None
    department_id: UUID | None = None
    user_level: str | None = None

class SuperAdminCreate(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    password: str
    confirm_password: str
    is_active: bool = True

class SuperAdminUpdate(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    email: EmailStr | None = None
    is_active: bool | None = None

# 🌟 SECURITY FIX: Strict schema to prevent Payload Injection / DoS via Date Parsing
class UserLeaveRequest(BaseModel):
    is_on_leave: bool = Field(..., description="Set to true to put user on leave")
    leave_start_date: datetime | None = None
    leave_end_date: datetime | None = None
    delegated_to_id: UUID | None = None