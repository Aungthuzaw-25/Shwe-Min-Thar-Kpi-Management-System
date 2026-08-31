from pydantic import BaseModel
from typing import List, Optional
from uuid import UUID

class PermissionResponse(BaseModel):
    id: UUID
    code: str
    description: str | None = None

    class Config:
        from_attributes = True

class AssignPermissionsRequest(BaseModel):
    permission_codes: List[str]

class UserPermissionOverrideRequest(BaseModel):
    permission_code: str
    allowed: bool
    scope: str = "ALL"
    department_id: Optional[UUID] = None

class UserPermissionOverrideResponse(BaseModel):
    permission_code: str
    description: Optional[str] = None
    allowed: bool
    scope: str
    department_id: Optional[UUID] = None