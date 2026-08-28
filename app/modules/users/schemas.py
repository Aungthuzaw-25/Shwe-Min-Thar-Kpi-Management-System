from pydantic import BaseModel, EmailStr, Field
from typing import Optional, List
from uuid import UUID
from datetime import datetime

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
    position: Optional[PositionResponseShort] = None   
    region_town: Optional[str] = None    
    is_active: bool
    role: Optional[RoleResponse] = None
    departments: List[UserDeptResponse] = []

    class Config:
        from_attributes = True

class UserCreate(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    password: Optional[str] = None
    position_id: Optional[UUID] = None
    region_town: Optional[str] = None    
    role_id: UUID
    is_active: bool = True
    department_id: Optional[UUID] = None
    user_level: Optional[str] = 'L2'

class UserUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[EmailStr] = None
    position_id: Optional[UUID] = None
    region_town: Optional[str] = None    
    role_id: Optional[UUID] = None
    is_active: Optional[bool] = None
    department_id: Optional[UUID] = None

# 🌟 SECURITY FIX: Strict schema to prevent Payload Injection / DoS via Date Parsing
class UserLeaveRequest(BaseModel):
    is_on_leave: bool = Field(..., description="Set to true to put user on leave")
    leave_start_date: Optional[datetime] = None
    leave_end_date: Optional[datetime] = None
    delegated_to_id: Optional[UUID] = None