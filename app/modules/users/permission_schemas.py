from pydantic import BaseModel
from typing import List, Optional
from uuid import UUID
from datetime import datetime

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


# =====================================================================
# 🌟 NEW: Effective Permission (merged role + user override view)
# =====================================================================
class EffectivePermissionItem(BaseModel):
    """A single permission entry showing the effective (resolved) state."""
    code: str
    description: Optional[str] = None
    allowed: bool
    source: str  # "role" | "override" | "denied_override" | "default"
    scope: str = "ALL"
    department_id: Optional[UUID] = None


class EffectivePermissionsResponse(BaseModel):
    user_id: UUID
    user_name: str
    user_email: str
    role_name: Optional[str] = None
    department_name: Optional[str] = None
    department_id: Optional[UUID] = None
    permissions: List[EffectivePermissionItem]


# =====================================================================
# 🌟 NEW: Bulk user override save
# =====================================================================
class BulkOverrideItem(BaseModel):
    permission_code: str
    allowed: bool
    scope: str = "ALL"
    department_id: Optional[UUID] = None


class BulkUserOverrideRequest(BaseModel):
    overrides: List[BulkOverrideItem]


# =====================================================================
# 🌟 NEW: Enriched audit log for the Audit History tab
# =====================================================================
class AuditLogDetailResponse(BaseModel):
    id: UUID
    action: str
    actor_id: Optional[UUID] = None
    actor_name: Optional[str] = None
    target_id: Optional[str] = None
    target_name: Optional[str] = None
    old_value: Optional[dict] = None
    new_value: Optional[dict] = None
    created_at: Optional[datetime] = None