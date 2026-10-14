from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, computed_field

from app.modules.users.avatar import avatar_color_index, avatar_initials, avatar_url


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
    email: str = Field(..., json_schema_extra={"format": "email"})
    position: PositionResponseShort | None = None
    region_town: str | None = None
    is_active: bool
    role: RoleResponse | None = None
    departments: list[UserDeptResponse] = []
    created_at: datetime | None = None
    last_login: datetime | None = None
    avatar_path: str | None = Field(default=None, exclude=True)
    avatar_updated_at: datetime | None = Field(default=None, exclude=True)

    class Config:
        from_attributes = True

    @computed_field
    @property
    def display_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @computed_field
    @property
    def avatar_url(self) -> str | None:
        return avatar_url(self.avatar_path, self.avatar_updated_at)

    @computed_field
    @property
    def initials(self) -> str:
        return avatar_initials(self.display_name, self.email)

    @computed_field
    @property
    def avatar_color_index(self) -> int:
        return avatar_color_index(self.id)

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