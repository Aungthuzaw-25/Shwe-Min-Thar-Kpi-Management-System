from pydantic import BaseModel
from typing import List
from uuid import UUID

class PermissionResponse(BaseModel):
    id: UUID
    code: str
    description: str | None = None

    class Config:
        from_attributes = True

class AssignPermissionsRequest(BaseModel):
    permission_codes: List[str]