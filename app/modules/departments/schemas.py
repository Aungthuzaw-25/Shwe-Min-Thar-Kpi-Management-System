from pydantic import BaseModel, Field
from typing import Optional, List
from uuid import UUID

class DepartmentCreate(BaseModel):
    name: str
    priority: int = Field(default=0, ge=0, le=100)

class DepartmentUpdate(BaseModel):
    name: Optional[str] = None
    priority: Optional[int] = Field(default=None, ge=0, le=100)

class DepartmentResponse(BaseModel):
    id: UUID
    name: str
    head_id: Optional[UUID] = None
    priority: int = 0
    class Config:
        from_attributes = True

class PositionInDepartment(BaseModel):
    id: UUID
    name: str
    # 🌟 FIX: Removed 'level' as it doesn't exist in Position model, added 'is_active'
    is_active: bool
    class Config:
        from_attributes = True

class DepartmentDetailResponse(DepartmentResponse):
    positions: List[PositionInDepartment] = []
    class Config:
        from_attributes = True

class AssignUserRequest(BaseModel):
    user_id: UUID
    user_level: str

class ChangeLevelRequest(BaseModel):
    user_level: str

class TransferUserRequest(BaseModel):
    user_id: UUID
    old_department_id: UUID
    new_department_id: UUID