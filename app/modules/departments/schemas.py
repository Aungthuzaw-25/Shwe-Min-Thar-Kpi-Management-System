from uuid import UUID

from pydantic import BaseModel


class DepartmentCreate(BaseModel):
    name: str

class DepartmentUpdate(BaseModel):
    name: str | None = None

class DepartmentResponse(BaseModel):
    id: UUID
    name: str
    head_id: UUID | None = None
    # Live count of current memberships (see repo.member_count_expr).
    # The departments.priority COLUMN still exists in the DB but is
    # dormant: it is no longer accepted, ordered by, or returned.
    member_count: int = 0
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
    positions: list[PositionInDepartment] = []
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
