from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class PermissionResponse(BaseModel):
    id: UUID
    code: str
    description: str | None = None

    class Config:
        from_attributes = True

class AssignPermissionsRequest(BaseModel):
    permission_codes: list[str]

class UserPermissionOverrideRequest(BaseModel):
    permission_code: str
    allowed: bool
    scope: str = "ALL"
    department_id: UUID | None = None

class UserPermissionOverrideResponse(BaseModel):
    permission_code: str
    description: str | None = None
    allowed: bool
    scope: str
    department_id: UUID | None = None


# =====================================================================
# 🌟 NEW: Effective Permission (merged role + user override view)
# =====================================================================
class EffectivePermissionItem(BaseModel):
    """A single permission entry showing the effective (resolved) state."""
    code: str
    description: str | None = None
    allowed: bool
    source: str  # "role" | "override" | "denied_override" | "default"
    scope: str = "ALL"
    department_id: UUID | None = None


class EffectivePermissionsResponse(BaseModel):
    user_id: UUID
    user_name: str
    user_email: str
    role_name: str | None = None
    department_name: str | None = None
    department_id: UUID | None = None
    permissions: list[EffectivePermissionItem]


# =====================================================================
# 🌟 NEW: Bulk user override save
# =====================================================================
class BulkOverrideItem(BaseModel):
    permission_code: str
    allowed: bool
    scope: str = "ALL"
    department_id: UUID | None = None


class BulkUserOverrideRequest(BaseModel):
    overrides: list[BulkOverrideItem]


# =====================================================================
# 🌟 NEW: Enriched audit log for the Audit History tab
# =====================================================================
class AuditLogDetailResponse(BaseModel):
    id: UUID
    action: str
    actor_id: UUID | None = None
    actor_name: str | None = None
    target_id: str | None = None
    target_name: str | None = None
    old_value: dict | None = None
    new_value: dict | None = None
    created_at: datetime | None = None